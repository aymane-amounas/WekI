#!/usr/bin/env python3
"""Start WekI on localhost and open it in the browser."""

from __future__ import annotations

import argparse
import threading
import webbrowser

import uvicorn

from weki.config import load
from weki.server import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, help="override the port from config.toml")
    args = parser.parse_args()

    config = load()
    port = args.port or config.port
    if not args.no_browser:
        url = f"http://{config.host}:{port}/"
        threading.Timer(1.0, webbrowser.open, args=[url]).start()
    uvicorn.run(create_app(config), host=config.host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
