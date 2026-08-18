"""Entry point so the dashboard can be started with a plain command."""

from __future__ import annotations

import sys
from pathlib import Path

from streamlit.web import cli as stcli

APP = Path(__file__).with_name("dashboard.py")


def main() -> int:
    sys.argv = ["streamlit", "run", str(APP), *sys.argv[1:]]
    return stcli.main()


if __name__ == "__main__":
    sys.exit(main())
