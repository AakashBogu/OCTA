"""Entry point for running OCTA via `python -m octa` or `octa` CLI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from octa import __version__
from octa.core.config import AppConfig
from octa.core.logging import setup_logging
from octa.desktop.application import OctaApplication


def create_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser with subcommands."""
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
    parser.add_argument(
        "--minimized",
        action="store_true",
        default=None,
        help="Start OCTA minimized to the system tray.",
    )
    parser.add_argument(
        "--no-auto-start",
        action="store_true",
        default=False,
        help="Do not automatically launch Godot companion on startup.",
    )
    parser.add_argument(
        "--headless-godot",
        action="store_true",
        default=False,
        help="Launch Godot companion in headless mode (useful for testing/automation).",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=None,
        help="Path to an optional custom .env configuration file.",
    )

    subparsers = parser.add_subparsers(dest="command", help="Optional CLI subcommands")

    # pack subcommand
    pack_parser = subparsers.add_parser(
        "pack",
        help="Pack an external tutorial plugin directory into a secure .octapack archive.",
    )
    pack_parser.add_argument(
        "source_dir",
        type=Path,
        help="Directory containing plugin.json and tutorial definitions.",
    )
    pack_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Target output archive path (default: <parent_dir>/<plugin_id>.octapack).",
    )
    pack_parser.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help="Overwrite destination archive if it already exists.",
    )

    # verify subcommand
    verify_parser = subparsers.add_parser(
        "verify",
        help="Inspect and verify an .octapack package without extracting.",
    )
    verify_parser.add_argument(
        "package_path",
        type=Path,
        help="Path to the .octapack or zip archive.",
    )
    verify_parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Display all file entries in the archive.",
    )

    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = create_parser()
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, run_event_loop: bool | None = None) -> int:
    """Launch OCTA application or execute CLI subcommand."""
    if argv is None:
        argv = sys.argv[1:]
    args = parse_args(argv)

    # Subcommand routing (Phase 9.1)
    if args.command == "pack":
        from octa.tutorials.packaging import run_pack_cli

        return run_pack_cli(args)

    if args.command == "verify":
        from octa.tutorials.packaging import run_verify_cli

        return run_verify_cli(args)

    # Honor CLI overrides for configuration
    overrides: dict[str, object] = {}
    if args.minimized is not None:
        overrides["start_minimized"] = True
    if args.no_auto_start:
        overrides["godot_auto_start"] = False

    config = AppConfig.load(env_file=args.config, **overrides)
    setup_logging(config)

    print(f"OCTA v{__version__} — PySide6 Desktop Interface (Phase 2.5)")

    if run_event_loop is None:
        run_event_loop = "pytest" not in sys.modules

    if not run_event_loop:
        return 0

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    app.setApplicationName("OCTA")

    _octa_app = OctaApplication(config, headless_godot=args.headless_godot)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
