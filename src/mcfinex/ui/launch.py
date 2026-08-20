"""Entry point so the dashboard can be started with a plain command."""

from __future__ import annotations

import sys
from pathlib import Path

from streamlit.web import cli as stcli

# The entry script must be the landing page: Streamlit builds its page
# navigation from the `pages/` directory beside it.
APP = Path(__file__).with_name("app.py")


def main() -> int:
    sys.argv = ["streamlit", "run", str(APP), *sys.argv[1:]]
    return stcli.main()


if __name__ == "__main__":
    sys.exit(main())
