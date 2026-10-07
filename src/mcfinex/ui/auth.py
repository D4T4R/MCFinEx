"""Who may make the app do work, as opposed to read it.

The dashboard is published to the internet, and until now anyone reaching it
could press a button that wrote to the production database, spent four requests
at screener.in from this project's address, and cleared a cache whose refill
re-screens two and a half thousand companies against a free-tier Postgres. None
of that is a reading action, and none of it should be available to a stranger.

Note what this is *not* for. It adds no protection against SQL injection, which
this codebase does not expose: every statement is parameterised, and the search
box filters a DataFrame in memory rather than reaching the database at all. The
risk being closed here is unauthenticated *writes* and the cost of unmetered
work, which authentication does address.

Three states, and they fail closed in all of them:

  - OIDC not configured at all: everyone reads, nobody writes. That has to stay
    usable, because the public read-only view is live now and its replacement is
    not built yet.
  - Configured, signed out or signed in but not on the allowlist: reads only.
  - Configured, signed in, on the allowlist: writes allowed.

An empty allowlist denies everyone. Letting it mean "everybody" is the kind of
default that turns one missing config line into an open door.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

#: Secrets key and environment variable holding the permitted addresses.
OPERATORS_KEY = "MCFINEX_OPERATORS"


def normalise(email: str | None) -> str:
    """An address in the one form comparisons are made in."""
    return (email or "").strip().lower()


def parse_operators(raw: object) -> frozenset[str]:
    """The allowlist, from a TOML list or a comma-separated string.

    Both shapes because the two places this is configured want different things:
    a Streamlit secrets file is naturally a list, and an environment variable
    cannot be one.
    """
    if raw is None:
        return frozenset()
    if isinstance(raw, str):
        candidates = raw.replace("\n", ",").split(",")
    else:
        try:
            candidates = list(raw)
        except TypeError:
            return frozenset()
    return frozenset(e for e in (normalise(str(c)) for c in candidates) if e)


def allows(email: str | None, allowlist: frozenset[str]) -> bool:
    """Whether this address may act.

    Separate from everything that touches Streamlit so the rule itself can be
    tested, because it is the rule that decides whether a stranger can write to
    the database.
    """
    if not allowlist:
        # No allowlist configured means nobody has been granted anything, not
        # that everybody has.
        return False
    return normalise(email) in allowlist


def operators() -> frozenset[str]:
    """The configured allowlist, from Streamlit secrets or the environment."""
    raw: object = None
    try:
        import streamlit as st

        raw = st.secrets[OPERATORS_KEY]
    except Exception:
        # No secrets file, no such key, or no Streamlit. All mean "not
        # configured here", and the environment is the other place to look.
        raw = None
    if raw is None:
        raw = os.environ.get(OPERATORS_KEY)
    return parse_operators(raw)


def signed_in_email() -> str | None:
    """The signed-in address, or None if nobody is signed in.

    ``st.user`` raises :class:`AttributeError` for any key it does not hold, and
    with OIDC unconfigured it holds none -- so the absence of configuration and
    the absence of a user arrive the same way, and both mean the same thing here.
    """
    try:
        import streamlit as st

        if not st.user.is_logged_in:
            return None
        return normalise(st.user.email)
    except Exception:
        return None


def auth_configured() -> bool:
    """Whether signing in is possible at all.

    Used only to decide whether offering a sign-in button would be honest. A
    button that raises ``StreamlitAuthError`` when pressed is worse than no
    button.
    """
    try:
        import streamlit as st

        return bool(st.secrets.get("auth", {}).get("redirect_uri"))
    except Exception:
        return False


def is_operator() -> bool:
    """Whether the current visitor may trigger work."""
    return allows(signed_in_email(), operators())


def sidebar_identity() -> bool:
    """Draw the sign-in state in the sidebar; return whether this is an operator.

    Deliberately quiet for an ordinary reader. Someone browsing the screen has no
    use for a login box, and an unexplained one invites them to wonder what they
    are missing -- so when nothing is configured, nothing is shown.
    """
    import streamlit as st

    email = signed_in_email()
    allowed = allows(email, operators())

    if allowed:
        st.sidebar.caption(f"Signed in as {email}")
        if st.sidebar.button("Sign out", width="stretch"):
            st.logout()
        return True

    if email is not None:
        # Signed in, but not on the list. Say so plainly rather than silently
        # showing a reader's view: they went out of their way to authenticate.
        st.sidebar.caption(f"{email} is not an operator on this deployment.")
        if st.sidebar.button("Sign out", width="stretch"):
            st.logout()
        return False

    if auth_configured():
        with st.sidebar.popover("Operator sign-in", width="stretch"):
            st.caption(
                "Signing in enables the actions that fetch new data. Everything "
                "else on this page is available without it."
            )
            if st.button("Continue with your provider", width="stretch"):
                st.login()
    return False
