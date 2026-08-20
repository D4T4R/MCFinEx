"""Copy a screened database from one store to another.

Used to push a locally built SQLite database up to the hosted Postgres the
deployed app reads. Deliberately one-way and destructive at the table level: the
source is the thing that was scraped and computed, so the target is replaced
rather than merged. Merging two versions of the same screen would leave a
mixture with no clear as-of date.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .db.store import COMPANY_COLUMNS, Store

#: Copied in this order so the foreign keys on financials and valuations always
#: have their company row already present.
TABLES = ("companies", "financials", "valuations", "result_calendar")

#: Rows per round trip. Large enough that 1.4 million rows do not take all day,
#: small enough not to build a single enormous statement.
BATCH = 5_000


@dataclass
class Migration:
    copied: dict[str, int] = field(default_factory=dict)
    skipped_financials: int = 0

    @property
    def total(self) -> int:
        return sum(self.copied.values())


def _columns(table: str) -> list[str]:
    if table == "companies":
        return ["ticker", *COMPANY_COLUMNS]
    if table == "financials":
        return ["ticker", "period", "statement", "label", "value"]
    if table == "valuations":
        return ["ticker", "model", "field", "value", "computed_at"]
    return ["ticker", "result_date", "financial_quarter"]


def migrate(source: Store, target: Store, *, only_screened: bool = True,
            progress=None) -> Migration:
    """Replace ``target``'s contents with ``source``'s.

    ``only_screened`` drops companies that were seeded from the bhavcopy but
    never scraped -- they carry a price and nothing else, and the deployed app
    has no use for them.
    """
    result = Migration()
    target.create_schema()
    _clear(target)

    tickers = set(source.scraped_tickers()) if only_screened else None

    for table in TABLES:
        columns = _columns(table)
        placeholders = ", ".join("?" for _ in columns)
        insert = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"

        rows = source.conn.execute(f"SELECT {', '.join(columns)} FROM {table}")
        batch: list[tuple] = []
        copied = 0
        for row in rows:
            if tickers is not None and row["ticker"] not in tickers:
                continue
            batch.append(tuple(row[c] for c in columns))
            if len(batch) >= BATCH:
                target.conn.executemany(insert, batch)
                copied += len(batch)
                batch.clear()
                if progress:
                    progress(table, copied)
        if batch:
            target.conn.executemany(insert, batch)
            copied += len(batch)
        target.conn.commit()
        result.copied[table] = copied
        if progress:
            progress(table, copied)
    return result


def _clear(target: Store) -> None:
    """Empty the target, children first so foreign keys never block a delete."""
    for table in reversed(TABLES):
        target.conn.execute(f"DELETE FROM {table}")
    target.conn.commit()


def compare(source: Store, target: Store) -> dict[str, tuple[int, int]]:
    """Row counts on both sides, so a migration can be checked rather than assumed."""
    counts: dict[str, tuple[int, int]] = {}
    for table in TABLES:
        a = source.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        b = target.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        counts[table] = (a, b)
    return counts
