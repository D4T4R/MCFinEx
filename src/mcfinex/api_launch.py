"""Entry point for the read-only API."""

from __future__ import annotations

import os


def main() -> None:
    import uvicorn

    uvicorn.run(
        "mcfinex.api:app",
        host=os.environ.get("MCFINEX_API_HOST", "127.0.0.1"),
        port=int(os.environ.get("MCFINEX_API_PORT", "8000")),
        reload=False,
    )


if __name__ == "__main__":
    main()
