"""Entry point for running OCTA via `python -m octa`."""

from __future__ import annotations

import argparse
import sys

from octa import __version__


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="octa",
        description="OCTA — On-screen Companion Tutor & Assistant",
    )
    parser.add_argument(
        "--version",
        "-V",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show program's version number and exit.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    parse_args(argv)
    print(f"OCTA v{__version__} - Python Application Foundation (Milestone 2.1)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
