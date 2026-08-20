"""Translating one set of SQL statements to run on SQLite or Postgres.

Every query in :mod:`mcfinex.db.store` is written once, in SQLite's style, with
``?`` placeholders. This module rewrites those statements for Postgres rather
than keeping two copies that can drift apart.

Kept deliberately small. The alternative is an ORM, which for twenty-odd
hand-written statements would be a much larger dependency than the problem
warrants.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: SQLite's storage classes mapped to Postgres types. SQLite is loosely typed
#: and will accept anything; Postgres will not.
_TYPES = (
    (re.compile(r"\bINTEGER\b", re.I), "INTEGER"),
    (re.compile(r"\bREAL\b", re.I), "DOUBLE PRECISION"),
    (re.compile(r"\bTEXT\b", re.I), "TEXT"),
)

#: Statements Postgres has no equivalent for and does not need.
_SQLITE_ONLY = re.compile(r"^\s*PRAGMA\b", re.I)

#: A quoted string literal, so placeholders inside one are left alone.
_LITERAL = re.compile(r"'(?:[^']|'')*'")

#: A line comment. Stripped before splitting, because a semicolon inside one
#: would otherwise cut a statement in half: the schema contains the comment
#: "-- hand; replaces STOCKS_RESULTS_FOR_CURRENT_QUARTER.", which split into a
#: comment-only fragment and a CREATE TABLE prefixed by orphaned prose.
_LINE_COMMENT = re.compile(r"--[^\n]*")


@dataclass(frozen=True)
class Dialect:
    """How to speak to one kind of database."""

    name: str
    placeholder: str

    @property
    def is_postgres(self) -> bool:
        return self.name == "postgres"

    def statement(self, sql: str) -> str:
        """Rewrite a SQLite-style statement for this dialect."""
        if not self.is_postgres:
            return sql
        return _to_postgres(sql)

    def schema(self, ddl: str) -> str:
        """Rewrite the schema for this dialect."""
        if not self.is_postgres:
            return ddl
        out = _to_postgres(ddl)
        for pattern, replacement in _TYPES:
            out = pattern.sub(replacement, out)
        # SQLite tolerates a trailing "WITHOUT ROWID" and similar; the schema
        # here uses none, so only the types and quoting differ.
        return out


SQLITE = Dialect(name="sqlite", placeholder="?")
POSTGRES = Dialect(name="postgres", placeholder="%s")


def _to_postgres(sql: str) -> str:
    """Swap ``?`` for ``%s`` and escape literal percent signs.

    psycopg treats ``%`` as the start of a placeholder, so a ``LIKE 'a%'``
    written for SQLite would be misread. Both substitutions skip anything
    inside a quoted literal, so a question mark in a company name survives.
    """
    pieces: list[str] = []
    last = 0
    for literal in _LITERAL.finditer(sql):
        pieces.append(_rewrite(sql[last:literal.start()]))
        # Inside a literal only the percent sign needs protecting.
        pieces.append(literal.group(0).replace("%", "%%"))
        last = literal.end()
    pieces.append(_rewrite(sql[last:]))
    return "".join(pieces)


def _rewrite(fragment: str) -> str:
    return fragment.replace("%", "%%").replace("?", "%s")


def split_statements(script: str) -> list[str]:
    """Break a schema script into executable statements.

    ``executescript`` is SQLite's; psycopg wants them one at a time. Comments
    are stripped first: a semicolon inside one splits a statement in half, and
    the result still looks plausible enough to ship.
    """
    without_comments = _LINE_COMMENT.sub("", script)
    return [
        statement.strip()
        for statement in without_comments.split(";")
        if statement.strip() and not _SQLITE_ONLY.match(statement)
    ]


def for_dsn(dsn: str) -> Dialect:
    """Which dialect a connection string implies."""
    return POSTGRES if dsn.startswith(("postgres://", "postgresql://")) else SQLITE
