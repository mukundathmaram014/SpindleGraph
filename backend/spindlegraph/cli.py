"""One-command launcher: ``spindlegraph`` (or ``python -m spindlegraph.cli``)
serves the API plus the built UI and opens a browser tab."""
from __future__ import annotations

import argparse
import threading
import webbrowser

import uvicorn


def main() -> None:
    p = argparse.ArgumentParser(prog="spindlegraph")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--no-browser", action="store_true",
                   help="don't open a browser tab on start")
    args = p.parse_args()
    if not args.no_browser:
        threading.Timer(
            1.2, webbrowser.open, [f"http://{args.host}:{args.port}"]).start()
    uvicorn.run("spindlegraph.main:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
