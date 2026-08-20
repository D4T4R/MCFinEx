"""SQL translation between SQLite and Postgres.

This is the part of the port that can be wrong silently: a mangled placeholder
or an unescaped percent sign produces a query that still runs and returns the
wrong rows. Tested exhaustively so the only unverified thing about the Postgres
path is the connection itself.
"""

from __future__ import annotations

import pytest

from mcfinex.db.dialect import POSTGRES, SQLITE, for_dsn, split_statements


class TestPlaceholders:
    def test_sqlite_is_left_alone(self):
        sql = "SELECT * FROM t WHERE a = ? AND b = ?"
        assert SQLITE.statement(sql) == sql

    def test_postgres_gets_percent_s(self):
        assert POSTGRES.statement("SELECT * FROM t WHERE a = ?") == \
            "SELECT * FROM t WHERE a = %s"

    def test_every_placeholder_is_converted(self):
        out = POSTGRES.statement("INSERT INTO t VALUES (?, ?, ?, ?)")
        assert out.count("%s") == 4
        assert "?" not in out

    def test_a_question_mark_inside_a_literal_survives(self):
        # A company named "Who? Ltd" must not become a bind parameter.
        out = POSTGRES.statement("SELECT * FROM t WHERE name = 'Who? Ltd' AND a = ?")
        assert "'Who? Ltd'" in out
        assert out.count("%s") == 1


class TestPercentEscaping:
    def test_like_pattern_in_a_literal_is_escaped(self):
        # psycopg reads % as a placeholder marker, so LIKE 'INF%' would break.
        out = POSTGRES.statement("SELECT * FROM t WHERE isin LIKE 'INF%'")
        assert "'INF%%'" in out

    def test_percent_outside_a_literal_is_escaped_too(self):
        assert POSTGRES.statement("SELECT 10 % 3") == "SELECT 10 %% 3"

    def test_escaping_and_placeholders_coexist(self):
        out = POSTGRES.statement("SELECT * FROM t WHERE a LIKE 'x%' AND b = ?")
        assert "'x%%'" in out and out.count("%s") == 1

    def test_sqlite_never_escapes(self):
        sql = "SELECT * FROM t WHERE isin LIKE 'INF%'"
        assert SQLITE.statement(sql) == sql


class TestQuotedLiterals:
    def test_doubled_quotes_inside_a_literal_are_handled(self):
        sql = "SELECT * FROM t WHERE name = 'O''Reilly?' AND a = ?"
        out = POSTGRES.statement(sql)
        assert "'O''Reilly?'" in out
        assert out.count("%s") == 1

    def test_multiple_literals(self):
        out = POSTGRES.statement("SELECT * FROM t WHERE a = 'x?' OR b = 'y%' OR c = ?")
        assert "'x?'" in out and "'y%%'" in out and out.count("%s") == 1


class TestSchema:
    def test_real_becomes_double_precision(self):
        assert "DOUBLE PRECISION" in POSTGRES.schema("CREATE TABLE t (v REAL)")
        assert "REAL" not in POSTGRES.schema("CREATE TABLE t (v REAL)")

    def test_text_and_integer_are_unchanged(self):
        out = POSTGRES.schema("CREATE TABLE t (a TEXT, b INTEGER)")
        assert "TEXT" in out and "INTEGER" in out

    def test_sqlite_schema_is_untouched(self):
        ddl = "CREATE TABLE t (v REAL)"
        assert SQLITE.schema(ddl) == ddl

    def test_the_real_schema_converts(self):
        from mcfinex.db.store import SCHEMA_PATH

        converted = POSTGRES.schema(SCHEMA_PATH.read_text())
        assert "DOUBLE PRECISION" in converted
        assert "?" not in converted


class TestSplitStatements:
    def test_splits_on_semicolons(self):
        assert len(split_statements("CREATE TABLE a (x TEXT); CREATE TABLE b (y TEXT);")) == 2

    def test_pragmas_are_dropped(self):
        # Postgres has no PRAGMA; keeping one would abort the whole script.
        statements = split_statements("PRAGMA foreign_keys = ON; CREATE TABLE a (x TEXT);")
        assert len(statements) == 1
        assert "CREATE TABLE" in statements[0]

    def test_blank_fragments_are_ignored(self):
        assert split_statements(";;  ;") == []

    def test_a_semicolon_inside_a_comment_does_not_split(self):
        # The schema contains "-- hand; replaces STOCKS_RESULTS...", which a
        # naive split cut in half, leaving orphaned prose before a CREATE TABLE.
        script = "-- a; b\nCREATE TABLE t (x TEXT);"
        statements = split_statements(script)
        assert len(statements) == 1
        assert statements[0].startswith("CREATE TABLE")

    def test_every_statement_from_the_real_schema_is_executable(self):
        from mcfinex.db.store import SCHEMA_PATH

        statements = split_statements(SCHEMA_PATH.read_text())
        assert len(statements) == 5
        for statement in statements:
            assert statement.upper().startswith(("CREATE TABLE", "CREATE INDEX")), statement

    def test_comments_are_removed_entirely(self):
        assert "--" not in " ".join(split_statements("-- note\nCREATE TABLE t (x TEXT);"))


class TestDsnDetection:
    @pytest.mark.parametrize("dsn", [
        "postgresql://user:pw@host:5432/db",
        "postgres://user:pw@host:5432/db",
    ])
    def test_postgres_urls(self, dsn):
        assert for_dsn(dsn).is_postgres

    @pytest.mark.parametrize("dsn", ["data/stocks.db", "/tmp/x.sqlite", ":memory:"])
    def test_anything_else_is_sqlite(self, dsn):
        assert not for_dsn(dsn).is_postgres


class TestRedaction:
    """A DSN must never reach a log, a terminal or CI output intact."""

    def test_password_is_masked(self):
        from mcfinex.config import redact

        out = redact("postgresql://postgres.abc:Sup3rSecret@host.supabase.com:5432/postgres")
        assert "Sup3rSecret" not in out
        assert "***" in out

    def test_host_and_user_survive_so_it_stays_useful(self):
        from mcfinex.config import redact

        out = redact("postgresql://postgres.abc:pw@host.supabase.com:5432/postgres")
        assert "postgres.abc" in out and "host.supabase.com" in out

    def test_a_password_containing_an_at_sign_is_still_masked(self):
        # Passwords are percent-encoded, but an unencoded @ must not fool it.
        from mcfinex.config import redact

        out = redact("postgresql://u:pa@ss@host:5432/db")
        assert "pa@ss" not in out

    def test_a_file_path_is_left_alone(self):
        from mcfinex.config import redact

        assert redact("data/stocks.db") == "data/stocks.db"

    def test_a_dsn_without_credentials_is_handled(self):
        from mcfinex.config import redact

        assert redact("postgresql://host:5432/db").startswith("postgresql://")
