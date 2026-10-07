"""Who may make the deployed app do work.

The dashboard is public, and one of its buttons wrote to the production database
and spent four requests at screener.in from this project's address. The rule that
decides who may press it is the whole of the protection, so it is tested as a
pure function rather than through Streamlit.

Every case here is written from the direction of failure: the question is never
"does an operator get in", it is "can anyone who is not one".
"""

from __future__ import annotations

import pytest

from mcfinex.ui import auth


class TestAllows:
    def test_an_allowlisted_address_may_act(self):
        assert auth.allows("you@example.com", frozenset({"you@example.com"}))

    def test_an_unknown_address_may_not(self):
        assert not auth.allows("stranger@example.com", frozenset({"you@example.com"}))

    def test_an_empty_allowlist_denies_everyone(self):
        # The important one. Treating "no list configured" as "no restriction"
        # turns one missing line of config into an open door, and the symptom
        # would be nothing at all until someone noticed the writes.
        assert not auth.allows("you@example.com", frozenset())

    def test_nobody_signed_in_may_not_act(self):
        assert not auth.allows(None, frozenset({"you@example.com"}))
        assert not auth.allows("", frozenset({"you@example.com"}))

    def test_case_and_whitespace_do_not_decide_access(self):
        allowlist = frozenset({"you@example.com"})
        assert auth.allows("YOU@Example.COM", allowlist)
        assert auth.allows("  you@example.com  ", allowlist)

    def test_a_similar_address_is_not_a_match(self):
        allowlist = frozenset({"you@example.com"})
        for near in ("you@example.co", "you@example.com.evil.test",
                     "xyou@example.com", "you@notexample.com"):
            assert not auth.allows(near, allowlist), near


class TestParseOperators:
    def test_a_toml_list(self):
        assert auth.parse_operators(["a@x.test", "b@x.test"]) == {"a@x.test", "b@x.test"}

    def test_a_comma_separated_string(self):
        # What an environment variable can carry.
        assert auth.parse_operators("a@x.test, b@x.test") == {"a@x.test", "b@x.test"}

    def test_newlines_are_separators_too(self):
        assert auth.parse_operators("a@x.test\nb@x.test") == {"a@x.test", "b@x.test"}

    def test_addresses_are_normalised_on_the_way_in(self):
        assert auth.parse_operators([" A@X.test "]) == {"a@x.test"}

    def test_blank_entries_are_dropped_not_kept_as_empty(self):
        # An empty string in the allowlist would match a signed-out visitor if
        # normalise(None) were ever compared against it.
        assert auth.parse_operators("a@x.test,,  ,") == {"a@x.test"}

    def test_absent_configuration_is_an_empty_allowlist(self):
        assert auth.parse_operators(None) == frozenset()
        assert auth.parse_operators("") == frozenset()
        assert auth.parse_operators([]) == frozenset()

    def test_an_unusable_value_does_not_become_a_permission(self):
        assert auth.parse_operators(12345) == frozenset()


class TestOperatorsFromConfiguration:
    def test_the_environment_supplies_the_allowlist(self, monkeypatch):
        monkeypatch.setenv(auth.OPERATORS_KEY, "a@x.test,b@x.test")
        assert auth.operators() == {"a@x.test", "b@x.test"}

    def test_nothing_configured_means_nobody(self, monkeypatch):
        monkeypatch.delenv(auth.OPERATORS_KEY, raising=False)
        assert auth.operators() == frozenset()

    def test_streamlit_secrets_win_over_the_environment(self, monkeypatch):
        # Deployment configuration should beat whatever is in the shell, the same
        # way MCFINEX_PG does.
        monkeypatch.setenv(auth.OPERATORS_KEY, "shell@x.test")
        monkeypatch.setattr(auth, "operators", auth.operators)

        import streamlit as st

        class Secrets:
            def __getitem__(self, key):
                if key == auth.OPERATORS_KEY:
                    return ["secret@x.test"]
                raise KeyError(key)

        monkeypatch.setattr(st, "secrets", Secrets(), raising=False)
        assert auth.operators() == {"secret@x.test"}


class TestSignedInEmail:
    """``st.user`` raises AttributeError for any key it does not hold.

    With OIDC unconfigured it holds none, so "not set up" and "nobody signed in"
    arrive identically -- and both have to mean the same thing, because the first
    is the state the deployment is in right now.
    """

    def _user(self, monkeypatch, **tokens):
        import streamlit as st

        class User:
            def __getattr__(self, key):
                try:
                    return tokens[key]
                except KeyError:
                    raise AttributeError(key)

        monkeypatch.setattr(st, "user", User(), raising=False)

    def test_a_signed_in_user_is_reported(self, monkeypatch):
        self._user(monkeypatch, is_logged_in=True, email="You@Example.com")
        assert auth.signed_in_email() == "you@example.com"

    def test_a_signed_out_user_is_none(self, monkeypatch):
        self._user(monkeypatch, is_logged_in=False)
        assert auth.signed_in_email() is None

    def test_unconfigured_auth_is_none_not_a_crash(self, monkeypatch):
        self._user(monkeypatch)   # no tokens at all: AttributeError on access
        assert auth.signed_in_email() is None

    def test_a_signed_in_user_with_no_email_is_not_an_operator(self, monkeypatch):
        # A provider that returns no email claim must not resolve to "" and then
        # match a blank allowlist entry.
        self._user(monkeypatch, is_logged_in=True)
        assert auth.signed_in_email() is None


class TestIsOperator:
    """The two halves together, which is what the write paths actually call."""

    def _state(self, monkeypatch, *, email, allowlist):
        monkeypatch.setattr(auth, "signed_in_email", lambda: email)
        monkeypatch.setattr(auth, "operators", lambda: frozenset(allowlist))

    def test_allowlisted_and_signed_in(self, monkeypatch):
        self._state(monkeypatch, email="you@x.test", allowlist={"you@x.test"})
        assert auth.is_operator()

    def test_signed_in_but_not_allowlisted(self, monkeypatch):
        self._state(monkeypatch, email="stranger@x.test", allowlist={"you@x.test"})
        assert not auth.is_operator()

    def test_allowlisted_but_not_signed_in(self, monkeypatch):
        self._state(monkeypatch, email=None, allowlist={"you@x.test"})
        assert not auth.is_operator()

    def test_auth_not_configured_at_all(self, monkeypatch):
        # The state the deployment is in today: readable by everyone, writable by
        # no one.
        self._state(monkeypatch, email=None, allowlist=set())
        assert not auth.is_operator()
