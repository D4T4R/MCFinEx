"""Serve a locally built `site/` for development.

GitHub Pages sets `Access-Control-Allow-Origin: *` and gzips on the fly. A
native build never sees CORS at all, but the web target does, so without this
the app runs against Pages and fails against a local copy -- which is exactly
backwards for development.

    python scripts/serve_site.py [--dir site] [--port 8531]
"""

from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class CorsHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        # The data is rebuilt on every publish; a cached copy would hide the
        # change being tested.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        # One line per request, without the date noise the default prints.
        print(f"{self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default="site")
    parser.add_argument("--port", type=int, default=8531)
    args = parser.parse_args()

    handler = partial(CorsHandler, directory=args.dir)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"serving {args.dir} on http://127.0.0.1:{args.port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
