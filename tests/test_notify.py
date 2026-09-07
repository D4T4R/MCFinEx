"""Delivering alerts over FCM.

Three things here are not cosmetic.

The topic names are a contract with a sideloaded app: a phone subscribed to a
name nobody publishes to hears nothing, and has no way to find out. So the
names are asserted against the app source rather than trusted to stay in step.

The state must not advance past a failed send. Advancing it consumes the
transition, and the next run sees nothing to report -- so the alert is not
delayed, it is gone.

And nothing may put the service account key into a log. This repository is
public and CI output with it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mcfinex.alerts import Alert, Snapshot, Trigger
from mcfinex.db.store import Store
from mcfinex.notify import (
    TOPIC_DAILY_PICK, TOPIC_ENTRY_REACHED, TOPIC_HIGH_CONVICTION, FcmError,
    collapse, condition_for, default_rules, deliver, load_credentials,
    ticker_topic,
)
from mcfinex.picks import Tier

from .test_picks import make_row

APP_NOTIFICATIONS = Path(__file__).resolve().parents[1] / "app" / "src" / "notifications.ts"


def alert(ticker="ACME", trigger=Trigger.ENTRY_REACHED, headline="something", detail="d"):
    return Alert(ticker, f"{ticker} Ltd", trigger, headline, detail)


class TestTopicNames:
    """The half of the contract that lives in this repository."""

    def test_the_app_declares_the_same_broadcast_topics(self):
        # The app is installed by hand, so a rename here reaches nobody's phone.
        # Parsing the source is ugly; shipping a topic nobody listens to is worse.
        source = APP_NOTIFICATIONS.read_text(encoding="utf-8")
        declared = set(re.findall(r"id:\s*'([a-z-]+)'", source))
        assert declared == {TOPIC_ENTRY_REACHED, TOPIC_HIGH_CONVICTION, TOPIC_DAILY_PICK}

    def test_the_app_builds_the_per_company_topic_the_same_way(self):
        source = APP_NOTIFICATIONS.read_text(encoding="utf-8")
        assert "`t_${id}`" in source
        assert ticker_topic("ACME") == "t_ACME"

    def test_the_per_company_topic_uses_the_published_id_not_the_ticker(self):
        # The app only knows a company by the id it was published under. Keying
        # on the raw ticker would have M&M subscribe to t_M&M and be sent t_M_M.
        assert ticker_topic("M&M") == "t_M_M"

    def test_topic_names_are_legal_for_fcm(self):
        legal = re.compile(r"^[a-zA-Z0-9\-_.~%]+$")
        for name in (TOPIC_ENTRY_REACHED, TOPIC_HIGH_CONVICTION, TOPIC_DAILY_PICK,
                     ticker_topic("M&M"), ticker_topic("BAJAJ-AUTO")):
            assert legal.match(name), name


class TestCondition:
    def test_a_broadcast_alert_reaches_both_audiences_once(self):
        # The point of a condition: someone subscribed to entry-reached *and*
        # watching ACME gets one notification, not two.
        assert condition_for(alert()) == (
            "'entry-reached' in topics || 't_ACME' in topics"
        )

    def test_an_alert_with_no_broadcast_topic_reaches_only_watchers(self):
        signal = alert(trigger=Trigger.SIGNAL_CHANGED)
        assert condition_for(signal) == "'t_ACME' in topics"

    def test_every_alert_reaches_the_people_watching_that_company(self):
        for trigger in Trigger:
            assert "'t_ACME' in topics" in condition_for(alert(trigger=trigger))

    def test_tier_left_is_not_broadcast(self):
        # Someone who asked for "new high-conviction name" did not ask to be
        # told when one leaves; that is only interesting if you hold it.
        assert condition_for(alert(trigger=Trigger.TIER_LEFT)) == "'t_ACME' in topics"


class TestBroadcastAssumption:
    def test_no_rule_enters_a_tier_other_than_high_conviction(self):
        # _BROADCAST maps TIER_ENTERED to high-conviction unconditionally,
        # because an Alert does not carry the tier it was raised for. Adding a
        # watch-list rule would push watch entries to people who asked for high
        # conviction, and nothing else would notice.
        entered = [r for r in default_rules() if r.trigger is Trigger.TIER_ENTERED]
        assert entered, "the high-conviction topic would have no publisher"
        assert all(r.tier is Tier.HIGH_CONVICTION for r in entered)


class TestCollapse:
    """One company, one buzz.

    The triggers are correlated -- a company that falls to its entry price has
    usually just entered the top tier too -- so without this a single event
    sends three notifications about the same name.
    """

    def test_three_alerts_about_one_company_become_one_notification(self):
        alerts = [alert(trigger=Trigger.ENTRY_REACHED),
                  alert(trigger=Trigger.TIER_ENTERED),
                  alert(trigger=Trigger.DAILY_PICK)]
        assert len(collapse(alerts)) == 1

    def test_the_highest_weighted_alert_supplies_the_headline(self):
        # collapse() takes the first of each group, and evaluate() sorts by
        # descending weight, so the lead is the most important thing that
        # happened rather than whichever rule ran first.
        alerts = [alert(trigger=Trigger.ENTRY_REACHED, headline="reached entry"),
                  alert(trigger=Trigger.DAILY_PICK, headline="todays pick")]
        assert collapse(alerts)[0].alert.headline == "reached entry"

    def test_nobody_who_had_a_reason_to_hear_is_dropped(self):
        # The failure this prevents: a daily-pick subscriber who is not watching
        # ACME hearing nothing because the entry-reached message won.
        alerts = [alert(trigger=Trigger.ENTRY_REACHED),
                  alert(trigger=Trigger.DAILY_PICK)]
        condition = collapse(alerts)[0].condition
        assert "'entry-reached' in topics" in condition
        assert "'daily-pick' in topics" in condition
        assert "'t_ACME' in topics" in condition

    def test_the_alerts_that_did_not_lead_are_still_named(self):
        alerts = [alert(trigger=Trigger.ENTRY_REACHED, detail="100 is 5% below"),
                  alert(trigger=Trigger.DAILY_PICK, headline="Todays pick: ACME")]
        assert "Also: Todays pick: ACME." in collapse(alerts)[0].body

    def test_different_companies_stay_separate(self):
        assert len(collapse([alert("ACME"), alert("BETA")])) == 2

    def test_a_condition_never_exceeds_the_five_topics_fcm_allows(self):
        alerts = [alert(trigger=t) for t in Trigger]
        assert collapse(alerts)[0].condition.count(" || ") < 5

    def test_a_lone_alert_keeps_its_own_detail_untouched(self):
        assert collapse([alert(detail="just this")])[0].body == "just this"


class TestMessage:
    @staticmethod
    def _message(a=None):
        return collapse([a or alert()])[0].message()

    def test_data_values_are_all_strings(self):
        # FCM rejects the whole request if one is a number, which is the sort of
        # thing that only shows up the first night it runs for real.
        assert all(isinstance(v, str) for v in self._message()["data"].values())

    def test_it_carries_the_published_id_so_a_tap_can_find_the_company(self):
        assert self._message(alert(ticker="M&M"))["data"]["id"] == "M_M"

    def test_a_missing_detail_is_an_empty_body_not_a_none(self):
        message = self._message(Alert("ACME", None, Trigger.DAILY_PICK, "h", ""))
        assert message["notification"]["body"] == ""

    def test_it_serialises(self):
        json.dumps({"message": self._message()})


class FakeSender:
    """Records what it was asked to send, and can be told to fail."""

    def __init__(self, fail_on: set[str] | None = None):
        self.sent: list[dict] = []
        self.fail_on = fail_on or set()

    def send(self, message):
        ticker = message["data"]["ticker"]
        if ticker in self.fail_on:
            raise FcmError(f"FCM returned 400: no good, {ticker}")
        self.sent.append(message)


class TestDeliver:
    def test_it_reports_what_went_out(self):
        sender = FakeSender()
        result = deliver(sender, collapse([alert("ACME"), alert("BETA")]))
        assert result.ok
        assert len(sender.sent) == 2

    def test_one_failure_does_not_abandon_the_rest(self):
        sender = FakeSender(fail_on={"ACME"})
        result = deliver(sender, collapse([alert("ACME"), alert("BETA")]))
        assert [m["data"]["ticker"] for m in sender.sent] == ["BETA"]
        assert not result.ok
        assert len(result.failed) == 1

    def test_a_failure_is_carried_back_not_swallowed(self):
        result = deliver(FakeSender(fail_on={"ACME"}), collapse([alert("ACME")]))
        assert "no good" in result.failed[0][1]


class TestAlertState:
    def test_it_round_trips(self, tmp_path):
        with Store(tmp_path / "s.db") as store:
            store.create_schema()
            store.save_alert_state({
                "ACME": Snapshot("high_conviction", True, 61.5, {"ROCE": "buy"}).as_dict(),
            })
            state = store.alert_state()
        assert state["ACME"]["tier"] == "high_conviction"
        assert state["ACME"]["actionable"] is True
        assert state["ACME"]["upside_pct"] == pytest.approx(61.5)
        assert state["ACME"]["verdicts"] == {"ROCE": "buy"}

    def test_it_survives_a_snapshot_round_trip(self, tmp_path):
        # The type the rules compare against has to come back out intact, or
        # every run looks like a transition.
        before = Snapshot("watch", False, 12.0, {"Current ratio": "hold"})
        with Store(tmp_path / "s.db") as store:
            store.create_schema()
            store.save_alert_state({"ACME": before.as_dict()})
            after = Snapshot.from_dict(store.alert_state()["ACME"])
        assert after == before

    def test_saving_again_replaces_rather_than_duplicates(self, tmp_path):
        with Store(tmp_path / "s.db") as store:
            store.create_schema()
            store.save_alert_state({"ACME": Snapshot("watch").as_dict()})
            store.save_alert_state({"ACME": Snapshot("high_conviction").as_dict()})
            state = store.alert_state()
        assert len(state) == 1
        assert state["ACME"]["tier"] == "high_conviction"

    def test_an_empty_save_is_not_an_error(self, tmp_path):
        with Store(tmp_path / "s.db") as store:
            store.create_schema()
            assert store.save_alert_state({}) == 0

    def test_unreadable_verdicts_do_not_crash_the_run(self, tmp_path):
        # One malformed column should cost that company its history, not take
        # the whole nightly job down.
        with Store(tmp_path / "s.db") as store:
            store.create_schema()
            store.save_alert_state({"ACME": Snapshot("watch").as_dict()})
            store.conn.execute("UPDATE alert_state SET verdicts = ? WHERE ticker = ?",
                               ("{not json", "ACME"))
            store.conn.commit()
            assert store.alert_state()["ACME"]["verdicts"] == {}


class TestTheNightlyRun:
    """`mcfinex notify` end to end, against a real database.

    Screening is stubbed -- these rows come from test_picks rather than a
    scrape -- but the state table, the rules, the delivery decision and the
    return codes are all the real ones.
    """

    @pytest.fixture
    def db(self, tmp_path):
        """A database from before alert_state existed.

        Deliberately not created with the current schema. The nightly job seeds,
        prices and publishes against a database that already exists and never
        runs `init`, so notify has to stand up its own table or the first real
        run dies on "no such table" -- which is what it did.
        """
        path = tmp_path / "notify.db"
        with Store(path) as store:
            store.create_schema()
            store.conn.execute("DROP TABLE alert_state")
            store.conn.commit()
        return str(path)

    @pytest.fixture
    def run(self, monkeypatch, db):
        from mcfinex import cli

        def go(rows, sender=None, argv=()):
            sender = sender if sender is not None else FakeSender()
            monkeypatch.setattr("mcfinex.report.screen_all",
                                lambda store, tickers=None: rows)
            monkeypatch.setattr("mcfinex.notify.load_credentials",
                                lambda raw=None: {"project_id": "test"})
            monkeypatch.setattr("mcfinex.notify.FcmSender",
                                lambda info, **kw: sender)
            code = cli.main(["--db", db, "notify", *argv])
            return code, sender

        go.db = db
        return go

    @staticmethod
    def _state(db):
        with Store(db) as store:
            return store.alert_state()

    def test_the_first_run_sends_nothing_and_records_a_baseline(self, run):
        # Otherwise night one pushes every company already below its entry.
        rows = [make_row(ticker=f"T{i}", price=100.0, target=200.0) for i in range(30)]
        code, sender = run(rows)
        assert code == 0
        assert sender.sent == []
        assert len(self._state(run.db)) == 30

    def test_a_transition_after_the_baseline_is_sent(self, run):
        expensive = [make_row(ticker="ACME", price=400.0, target=200.0)]
        run(expensive)                                   # baseline
        cheap = [make_row(ticker="ACME", price=100.0, target=200.0)]
        code, sender = run(cheap)
        assert code == 0
        assert [m["data"]["ticker"] for m in sender.sent] == ["ACME"]

    def test_one_company_produces_one_push_however_many_rules_matched(self, run):
        # ACME reaches its entry, enters high conviction and is the daily pick
        # in the same run. That is one event to a reader, not three.
        expensive = [make_row(ticker="ACME", price=400.0, target=200.0)]
        run(expensive)
        cheap = [make_row(ticker="ACME", price=100.0, target=200.0)]
        _, sender = run(cheap)
        assert len(sender.sent) == 1

    def test_a_settled_company_still_gets_the_daily_pick(self, run):
        # DAILY_PICK is not a transition -- it goes out whether or not anything
        # changed -- so this is the one rule that keeps firing. Asserting it so
        # that a future "send nothing when nothing changed" cannot break it.
        expensive = [make_row(ticker="ACME", price=400.0, target=200.0)]
        cheap = [make_row(ticker="ACME", price=100.0, target=200.0)]
        run(expensive)
        run(cheap)
        _, sender = run(cheap)
        assert [m["data"]["trigger"] for m in sender.sent] == ["daily_pick"]

    def test_a_failed_send_leaves_the_state_alone_so_it_retries(self, run):
        # The property this whole design turns on. If the state advanced here,
        # the transition would be consumed and the alert lost, not delayed.
        expensive = [make_row(ticker="ACME", price=400.0, target=200.0)]
        run(expensive)
        before = self._state(run.db)["ACME"]

        cheap = [make_row(ticker="ACME", price=100.0, target=200.0)]
        code, _ = run(cheap, sender=FakeSender(fail_on={"ACME"}))
        assert code == 1
        assert self._state(run.db)["ACME"] == before

        # And the next run sends it, rather than having quietly dropped it.
        _, sender = run(cheap)
        assert [m["data"]["ticker"] for m in sender.sent] == ["ACME"]

    def test_a_dry_run_does_not_lay_down_the_baseline_either(self, run):
        # The bug this caught: the baseline write sat before the dry-run check,
        # so previewing the first run consumed it. The real run that followed
        # found a history, saw no transitions, and sent nothing.
        rows = [make_row(ticker="ACME", price=100.0, target=200.0)]
        code, sender = run(rows, argv=["--dry-run"])
        assert code == 0
        assert sender.sent == []
        assert self._state(run.db) == {}

    def test_a_dry_run_neither_sends_nor_advances(self, run, capsys):
        expensive = [make_row(ticker="ACME", price=400.0, target=200.0)]
        run(expensive)
        before = self._state(run.db)["ACME"]

        cheap = [make_row(ticker="ACME", price=100.0, target=200.0)]
        code, sender = run(cheap, argv=["--dry-run"])
        assert code == 0
        assert sender.sent == []
        assert self._state(run.db)["ACME"] == before
        assert "ACME" in capsys.readouterr().out

    def test_the_limit_caps_one_night(self, run):
        baseline = [make_row(ticker=f"T{i}", price=400.0, target=200.0) for i in range(10)]
        run(baseline)
        crashed = [make_row(ticker=f"T{i}", price=100.0, target=200.0) for i in range(10)]
        _, sender = run(crashed, argv=["--limit", "3"])
        assert len(sender.sent) == 3

    def test_a_missing_credential_fails_the_job_rather_than_the_data(self, run,
                                                                    monkeypatch):
        from mcfinex.notify import FcmError

        expensive = [make_row(ticker="ACME", price=400.0, target=200.0)]
        run(expensive)
        before = self._state(run.db)["ACME"]

        from mcfinex import cli
        cheap = [make_row(ticker="ACME", price=100.0, target=200.0)]
        monkeypatch.setattr("mcfinex.report.screen_all", lambda store, tickers=None: cheap)

        def missing(raw=None):
            raise FcmError("MCFINEX_FCM is not set.")

        monkeypatch.setattr("mcfinex.notify.load_credentials", missing)
        assert cli.main(["--db", run.db, "notify"]) == 1
        assert self._state(run.db)["ACME"] == before


class FakeResponse:
    def __init__(self, status, payload=None):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._payload = payload if payload is not None else {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


def service_account_info():
    """A structurally real service account, with a key generated here.

    Built rather than stubbed so ``from_service_account_info`` actually parses
    it -- that call is the one place a malformed secret would surface, and a
    mock would not tell us it works.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return {
        "type": "service_account", "project_id": "mcfinex-test",
        "private_key_id": "abc", "private_key": pem,
        "client_email": "notify@mcfinex-test.iam.gserviceaccount.com",
        "client_id": "1", "token_uri": "https://oauth2.googleapis.com/token",
    }


@pytest.fixture(scope="module")
def info():
    """Generated once: an RSA keygen per test is a second of nothing."""
    return service_account_info()


class TestFcmSender:
    @pytest.fixture
    def sender(self, info, monkeypatch):
        from mcfinex.notify import FcmSender

        sender = FcmSender(info)
        # The token exchange is Google's to test; everything after it is ours.
        monkeypatch.setattr(sender, "_token", lambda: "an-access-token")
        monkeypatch.setattr("time.sleep", lambda _s: None)
        return sender

    def test_it_posts_to_the_project_from_the_credential(self, sender):
        assert sender.url == (
            "https://fcm.googleapis.com/v1/projects/mcfinex-test/messages:send"
        )

    def test_a_service_account_without_a_project_is_refused(self):
        from mcfinex.notify import FcmSender

        with pytest.raises(FcmError, match="project_id"):
            FcmSender({"type": "service_account"})

    def test_a_transient_failure_is_retried(self, sender, monkeypatch):
        import requests

        responses = [FakeResponse(503), FakeResponse(503), FakeResponse(200)]
        calls = []

        def post(url, **kwargs):
            calls.append(kwargs)
            return responses.pop(0)

        monkeypatch.setattr(requests, "post", post)
        sender.send({"condition": "x"})
        assert len(calls) == 3

    def test_a_bad_request_is_not_retried(self, sender, monkeypatch):
        # A 400 means the message is malformed and a 403 means the credential is
        # wrong. Retrying either just delays the report by six seconds.
        import requests

        calls = []

        def post(url, **kwargs):
            calls.append(kwargs)
            return FakeResponse(400, {"error": {"message": "bad condition"}})

        monkeypatch.setattr(requests, "post", post)
        with pytest.raises(FcmError, match="bad condition"):
            sender.send({"condition": "x"})
        assert len(calls) == 1

    def test_it_gives_up_after_three_transient_failures(self, sender, monkeypatch):
        import requests

        monkeypatch.setattr(requests, "post", lambda url, **kw: FakeResponse(503))
        with pytest.raises(FcmError, match="503"):
            sender.send({"condition": "x"})

    def test_a_dropped_connection_is_retried_not_raised_through(self, sender,
                                                                monkeypatch):
        # Letting requests' own exception escape would abandon every remaining
        # alert in the run, not just this one.
        import requests

        def post(url, **kwargs):
            raise requests.ConnectionError("reset by peer")

        monkeypatch.setattr(requests, "post", post)
        with pytest.raises(FcmError, match="could not reach FCM"):
            sender.send({"condition": "x"})

    def test_the_access_token_never_reaches_the_error_message(self, sender,
                                                              monkeypatch):
        # CI logs on this repository are public.
        import requests

        monkeypatch.setattr(requests, "post", lambda url, **kw: FakeResponse(
            400, {"error": {"message": "nope"}}))
        with pytest.raises(FcmError) as exc:
            sender.send({"condition": "x"})
        assert "an-access-token" not in str(exc.value)

    def test_a_response_that_is_not_json_still_reports_the_status(self, sender,
                                                                  monkeypatch):
        import requests

        class Html:
            status_code = 502
            ok = False
            text = "<html>gateway</html>"

            def json(self):
                raise ValueError("not json")

        monkeypatch.setattr(requests, "post", lambda url, **kw: Html())
        with pytest.raises(FcmError, match="502"):
            sender.send({"condition": "x"})


class TestPostgresSchema:
    """The state table has to exist on the hosted database too.

    Everything else in this file runs on SQLite. The nightly job runs on
    Postgres, and a DDL that only parses in one of them would fail there first.
    """

    def test_the_table_translates(self):
        from mcfinex.db.dialect import POSTGRES, split_statements
        from mcfinex.db.store import SCHEMA_PATH

        ddl = POSTGRES.schema(SCHEMA_PATH.read_text())
        table = next(s for s in split_statements(ddl) if "alert_state" in s)
        # REAL is not a Postgres type name for what this stores.
        assert "upside_pct  DOUBLE PRECISION" in table
        # And the JSON default has to survive the placeholder rewriting intact.
        assert "DEFAULT '{}'" in table

    def test_the_upsert_translates(self):
        from mcfinex.db.dialect import POSTGRES

        sql = POSTGRES.statement(
            "INSERT INTO alert_state (ticker, verdicts) VALUES (?, ?) "
            "ON CONFLICT(ticker) DO UPDATE SET verdicts = excluded.verdicts"
        )
        assert "VALUES (%s, %s)" in sql
        assert "?" not in sql


class TestCredentials:
    def test_it_says_what_to_do_when_the_secret_is_missing(self, monkeypatch):
        monkeypatch.delenv("MCFINEX_FCM", raising=False)
        with pytest.raises(FcmError, match="MCFINEX_FCM"):
            load_credentials()

    def test_it_reads_the_json_directly(self):
        info = load_credentials('{"project_id": "mcfinex-test"}')
        assert info["project_id"] == "mcfinex-test"

    def test_it_reads_a_path(self, tmp_path):
        path = tmp_path / "sa.json"
        path.write_text('{"project_id": "from-a-file"}')
        assert load_credentials(str(path))["project_id"] == "from-a-file"

    def test_a_missing_file_is_named_plainly(self, tmp_path):
        with pytest.raises(FcmError, match="no service account file"):
            load_credentials(str(tmp_path / "nope.json"))

    def test_malformed_json_never_echoes_the_key(self):
        # The value is either a filename or a private key, and this cannot tell
        # which. So it quotes neither.
        secret = '{"private_key": "-----BEGIN PRIVATE KEY-----\nhunter2", oops}'
        with pytest.raises(FcmError) as exc:
            load_credentials(secret)
        assert "hunter2" not in str(exc.value)
        assert "BEGIN PRIVATE KEY" not in str(exc.value)

    def test_a_file_of_valid_json_that_is_not_an_account_is_rejected(self, tmp_path):
        # Reached through the path branch: the inline branch keys on a leading
        # `{`, so a bare array there is read as a filename instead.
        path = tmp_path / "sa.json"
        path.write_text("[]")
        with pytest.raises(FcmError, match="JSON object"):
            load_credentials(str(path))
