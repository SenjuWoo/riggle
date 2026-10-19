#!/usr/bin/env python3
"""Launcher: python run.py [--port 8765] [--no-browser]"""

import argparse
import sys

from riggle import APP_NAME, VERSION
from riggle.server import serve


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} {VERSION}")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args()
    if args.version:
        print(VERSION)
        return 0
    serve(port=args.port, open_browser=not args.no_browser)
    return 0


if __name__ == "__main__":
    sys.exit(main())
