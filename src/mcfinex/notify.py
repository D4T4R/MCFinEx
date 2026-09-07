"""Deliver alerts to the phone, over FCM topics.

:mod:`mcfinex.alerts` decides *what* to say and stays pure. This decides who
hears it and puts it on the wire.

Topics rather than device tokens. A token-based design needs somewhere to
register the token, a table to keep it in and a write endpoint open to the
internet -- three moving parts, for an audience of about five people. With
topics the phone subscribes itself, so there is nothing here that knows who is
listening, and nothing to leak if this code is wrong.

That cuts both ways, and the cost is worth stating plainly: there is no way to
ask FCM who is subscribed, no way to send to one person, and no delivery
receipt. A send that returns 200 means Google accepted the message, not that a
phone showed it.

Deduplication is why messages go to a *condition* rather than a topic. Someone
watching RELIANCE is subscribed to both ``entry-reached`` and ``t_RELIANCE``;
publishing to each separately would buzz their phone twice for one event.
``'entry-reached' in topics || 't_RELIANCE' in topics`` delivers once to the
union, which is what was meant.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .alerts import Alert, Rule, Snapshot, Trigger, evaluate
from .picks import Tier
from .publish import file_id
from .screening import Verdict

log = logging.getLogger(__name__)

#: Broadcast topics. These strings are a contract with the phone: they must
#: match TOPICS in app/src/notifications.ts exactly, because a subscriber to a
#: name nobody publishes to hears nothing at all and the app cannot tell.
#: tests/test_notify.py reads that file and asserts they still line up.
TOPIC_ENTRY_REACHED = "entry-reached"
TOPIC_HIGH_CONVICTION = "high-conviction"
TOPIC_DAILY_PICK = "daily-pick"

def ticker_topic(ticker: str) -> str:
    """Mirrors ``tickerTopic`` in the app, which keys on the published file id.

    The app only ever knows a company by the id it was published under, so the
    topic has to be derived the same way -- from :func:`mcfinex.publish.file_id`
    rather than from the raw ticker, or M&M would subscribe to ``t_M&M`` and be
    sent ``t_M_M``.
    """
    return f"t_{file_id(ticker)}"


#: Which broadcast topic, if any, carries each trigger. Everything else reaches
#: only the people watching that specific company.
#:
#: TIER_ENTERED maps to high-conviction unconditionally, which is only correct
#: because :func:`default_rules` never asks for any other tier -- an Alert does
#: not carry the tier it was raised for, so this cannot check. Adding a
#: ``Rule(Trigger.TIER_ENTERED, tier=Tier.WATCH)`` would quietly push watch-list
#: entries to people who asked for high conviction. test_notify.py enforces the
#: assumption rather than leaving it as a comment nobody reads.
_BROADCAST: dict[Trigger, str] = {
    Trigger.ENTRY_REACHED: TOPIC_ENTRY_REACHED,
    Trigger.TIER_ENTERED: TOPIC_HIGH_CONVICTION,
    Trigger.DAILY_PICK: TOPIC_DAILY_PICK,
}

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


def default_rules() -> list[Rule]:
    """The rule set the nightly job runs.

    Limits are per rule and deliberately low. They are not about API quota --
    FCM would take far more -- but about the reader: a night that legitimately
    produces two hundred transitions is a night nobody reads any of them.

    The trade is real and worth naming. When more companies cross than the limit
    allows, the lowest-weighted are dropped and never resent, because the state
    advances for every company whether or not its alert was sent. Someone
    watching one of the dropped names will not hear about it. The alternative --
    tracking per-alert delivery so the tail can be resent tomorrow -- means the
    backlog arrives a day late, which for an entry price is worse than silence.
    """
    return [
        # Weighted highest in alerts.py, so this survives the global cap.
        Rule(Trigger.ENTRY_REACHED, limit=25),
        Rule(Trigger.TIER_ENTERED, tier=Tier.HIGH_CONVICTION, limit=25),
        Rule(Trigger.TIER_LEFT, tier=Tier.HIGH_CONVICTION, limit=25),
        # Only the turn to SELL. A signal drifting between BUY and HOLD is
        # noise; a company that has started failing one is worth a look.
        Rule(Trigger.SIGNAL_CHANGED, signal="Current ratio",
             to_verdict=Verdict.SELL, limit=10),
        Rule(Trigger.SIGNAL_CHANGED, signal="ROCE",
             to_verdict=Verdict.SELL, limit=10),
        Rule(Trigger.DAILY_PICK, limit=1),
    ]


def _topics_for(alert: Alert) -> list[str]:
    """Everyone with a reason to hear about this alert.

    Always includes the per-company topic, so watching a company means hearing
    everything about it rather than only the categories that happen to have a
    broadcast switch.
    """
    broadcast = _BROADCAST.get(alert.trigger)
    return ([broadcast] if broadcast else []) + [ticker_topic(alert.ticker)]


def condition_for(alert: Alert) -> str:
    """The FCM condition one alert on its own would be delivered to."""
    return " || ".join(f"'{t}' in topics" for t in _topics_for(alert))


@dataclass(frozen=True)
class Notification:
    """One push: the alert that titles it, and everyone it goes to."""

    alert: Alert
    condition: str
    body: str

    def message(self) -> dict[str, Any]:
        """One FCM HTTP v1 message body.

        ``data`` values must be strings; FCM rejects the request outright if any
        is a number, which is the kind of thing that only shows up the first
        night it runs for real.
        """
        return {
            "condition": self.condition,
            "notification": {"title": self.alert.headline, "body": self.body},
            "data": {
                "ticker": self.alert.ticker,
                "id": file_id(self.alert.ticker),
                "trigger": self.alert.trigger.value,
            },
        }


def collapse(alerts: Sequence[Alert]) -> list[Notification]:
    """At most one notification per company per run.

    The triggers are correlated, not independent: a company that falls far
    enough to reach its entry price has usually also just entered the top tier,
    and is a good candidate for the daily pick. Sent as three messages, that is
    a phone buzzing three times about one thing.

    They cannot simply be dropped, because each carries a different audience --
    a daily-pick subscriber who is not watching the company would hear nothing
    if the entry-reached message won and kept only its own condition. So the
    highest-weighted alert supplies the headline, and the condition is the union
    of every audience that had a reason to be told. The others are named in the
    body, so nothing is silently lost.

    Requires ``alerts`` sorted by descending weight, which is what
    :func:`mcfinex.alerts.evaluate` returns.
    """
    grouped: dict[str, list[Alert]] = {}
    for alert in alerts:
        grouped.setdefault(alert.ticker, []).append(alert)

    out: list[Notification] = []
    for group in grouped.values():
        lead = group[0]
        # dict.fromkeys rather than a set: FCM allows five topics in a
        # condition, so which ones survive a truncation should be the
        # highest-weighted, not whichever way a set happened to iterate.
        topics = list(dict.fromkeys(t for a in group for t in _topics_for(a)))
        condition = " || ".join(f"'{t}' in topics" for t in topics[:5])
        body = lead.detail or ""
        if len(group) > 1:
            also = "; ".join(a.headline for a in group[1:])
            body = f"{body} Also: {also}." if body else f"Also: {also}."
        out.append(Notification(lead, condition, body))
    return out


class FcmError(RuntimeError):
    """A send that failed for a reason retrying will not fix."""


@dataclass
class Sent:
    """What one run delivered."""

    delivered: list["Notification"] = field(default_factory=list)
    failed: list[tuple["Notification", str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed


class FcmSender:
    """Publishes to FCM HTTP v1 with a service-account credential.

    The legacy server-key API was turned off in 2024, so this is OAuth2: a
    short-lived access token minted from the service account, refreshed by
    google-auth rather than by hand-rolling RS256 here.
    """

    def __init__(self, credentials_info: dict[str, Any], *, timeout: float = 20.0):
        try:
            from google.auth.transport.requests import Request
            from google.oauth2 import service_account
        except ImportError as exc:  # pragma: no cover - depends on install
            raise FcmError(
                "Sending push needs the notify extra. Run: pip install '.[notify]'"
            ) from exc

        project = credentials_info.get("project_id")
        if not project:
            raise FcmError("the service account JSON has no project_id")
        self.project = project
        self._request = Request()
        self._credentials = service_account.Credentials.from_service_account_info(
            credentials_info, scopes=[FCM_SCOPE]
        )
        self.timeout = timeout
        self.url = f"https://fcm.googleapis.com/v1/projects/{project}/messages:send"

    def _token(self) -> str:
        if not self._credentials.valid:
            try:
                self._credentials.refresh(self._request)
            except Exception as exc:
                # google-auth raises its own exception hierarchy. Whatever it
                # is, the message can mention the account but never the key.
                raise FcmError(
                    f"could not get an access token for {self.project}: "
                    f"{type(exc).__name__}"
                ) from exc
        return self._credentials.token

    def send(self, message: dict[str, Any]) -> None:
        """Publish one message, retrying only what retrying can fix."""
        import requests

        last = ""
        for attempt in range(3):
            try:
                response = requests.post(
                    self.url,
                    headers={"Authorization": f"Bearer {self._token()}",
                             "Content-Type": "application/json; UTF-8"},
                    json={"message": message},
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                # A dropped connection is exactly the case retrying is for, and
                # letting it escape here would abandon the remaining alerts.
                last = f"could not reach FCM: {type(exc).__name__}"
                time.sleep(2 ** attempt)
                continue
            if response.ok:
                return
            last = _explain(response)
            # 429 and 5xx are the transient ones. A 400 means the message is
            # malformed and a 403 means the credential is wrong; both would fail
            # identically three times and only delay the report.
            if response.status_code not in (429, 500, 502, 503, 504):
                raise FcmError(last)
            time.sleep(2 ** attempt)
        raise FcmError(last)


def _explain(response) -> str:
    """FCM's error, without the credential that produced it.

    The token is in the request headers, not the body, so a response is safe to
    quote -- but only the body. Anything wider risks putting an Authorization
    header into CI logs, which is public on this repository.
    """
    try:
        payload = response.json().get("error", {})
        detail = payload.get("message") or payload.get("status") or ""
    except (ValueError, AttributeError):
        detail = (response.text or "")[:200]
    return f"FCM returned {response.status_code}: {detail}".strip()


def load_credentials(raw: str | None = None) -> dict[str, Any]:
    """The service account, from MCFINEX_FCM as JSON or as a path to it.

    Both forms because the two places this runs want different things: a GitHub
    secret is naturally the JSON itself, and a developer would rather not paste
    a private key into their shell history.

    Deliberately not part of :class:`mcfinex.config.Settings`. That object gets
    printed, logged and passed around; a signing key does not belong in it.
    """
    text = (raw if raw is not None else os.environ.get("MCFINEX_FCM", "")).strip()
    if not text:
        raise FcmError(
            "MCFINEX_FCM is not set. It holds the Firebase service account JSON "
            "(Project settings > Service accounts > Generate new private key), "
            "or a path to it."
        )
    if not text.startswith("{"):
        path = os.path.expanduser(text)
        if not os.path.exists(path):
            raise FcmError(f"no service account file at {path}")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    try:
        info = json.loads(text)
    except json.JSONDecodeError as exc:
        # Never echo the value: on the path branch it is a filename, but on the
        # other it is the private key itself.
        raise FcmError(f"MCFINEX_FCM is not valid JSON ({exc.msg})") from exc
    if not isinstance(info, dict):
        raise FcmError("MCFINEX_FCM should be a JSON object")
    return info


def evaluate_store(store, rules: Sequence[Rule] | None = None,
                   rows: Iterable | None = None) -> tuple[list[Alert], dict[str, Snapshot]]:
    """Screen, compare against stored state, and return what to send.

    The first run sends nothing and only records a baseline. Rules differ on
    what an absent history means -- TIER_ENTERED treats it as "not a
    transition", ENTRY_REACHED as "it is below its entry, say so" -- and both
    readings are right for a caller holding a partial state. But there is no
    reading under which the first night should push twenty-five notifications
    for companies that have been sitting below their entry price for months.
    That is a property of the run, not of any rule, so it is decided here.
    """
    from .report import screen_all

    rows = list(rows) if rows is not None else screen_all(store)
    previous = {t: Snapshot.from_dict(s) for t, s in store.alert_state().items()}
    alerts, state = evaluate(
        rows, list(rules if rules is not None else default_rules()), previous)
    if not previous:
        log.info("no alert history; recording a baseline for %d companies "
                 "and sending nothing", len(state))
        return [], state
    return alerts, state


def deliver(sender, notifications: Sequence[Notification]) -> Sent:
    """Send each notification, carrying on past a failure so one bad message
    does not hide the rest -- but recording it, because the caller uses that to
    decide whether the state may advance."""
    result = Sent()
    for notification in notifications:
        ticker = notification.alert.ticker
        try:
            sender.send(notification.message())
        except FcmError as exc:
            log.error("%s: %s", ticker, exc)
            result.failed.append((notification, str(exc)))
        else:
            log.info("sent %s to %s", notification.alert.trigger.value, ticker)
            result.delivered.append(notification)
    return result
