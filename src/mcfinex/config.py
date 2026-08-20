"""Runtime settings.

Everything is an environment variable with a working default, so the project
runs from a clean checkout on any OS. The Java build hardcoded absolute Windows
paths (``E:\\Selenium\\chromedriver.exe``, ``E://StockData.csv``) and could only
run on the machine it was written on.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _path(env: str, default: Path) -> Path:
    return Path(os.environ.get(env, default)).expanduser()


def _float(env: str, default: float) -> float:
    try:
        return float(os.environ[env])
    except (KeyError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    #: A local path, or a Postgres DSN when MCFINEX_PG is set.
    db_path: Path | str
    template_path: Path
    export_path: Path
    request_delay: float   # seconds between screener requests
    request_timeout: float

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = _path("MCFINEX_DATA_DIR", PROJECT_ROOT / "data")
        # A Postgres DSN is a connection string, not a path, so it must not be
        # expanded or resolved. MCFINEX_PG wins when both are set: it is what a
        # deployment sets, and a stale local file silently taking precedence
        # would be very hard to notice.
        dsn = os.environ.get("MCFINEX_PG", "").strip()
        return cls(
            data_dir=data_dir,
            db_path=dsn or _path("MCFINEX_DB", data_dir / "stocks.db"),
            # The SSP working workbook carries the valuation formulas; the
            # scraper only fills its input cells.
            template_path=_path("MCFINEX_TEMPLATE", Path.home() / "Downloads" / "SSP_Working_merged.xlsx"),
            export_path=_path("MCFINEX_EXPORT", data_dir / "SSP_Working_populated.xlsx"),
            # Screener is a free service; one request a second keeps us a well
            # behaved client rather than a load problem.
            request_delay=_float("MCFINEX_REQUEST_DELAY", 1.0),
            request_timeout=_float("MCFINEX_REQUEST_TIMEOUT", 20.0),
        )


settings = Settings.from_env()


def is_dsn(target: Path | str) -> bool:
    """Whether a setting points at a hosted database rather than a file."""
    return str(target).startswith(("postgres://", "postgresql://"))


def database_ready(settings: "Settings") -> bool:
    """A DSN is assumed reachable; a file has to exist."""
    return is_dsn(settings.db_path) or Path(settings.db_path).exists()
