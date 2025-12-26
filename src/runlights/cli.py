from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .ipc import IPCNotReady, send_console_request


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="runlights-cli", description="RunLights command-line interface")
    subparsers = parser.add_subparsers(dest="command", required=True)

    console_parser = subparsers.add_parser("console", help="Send console name (from ESDE) to the tray")
    console_parser.add_argument("name", help="Console name (e.g., snes, megadrive)")

    console_parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.toml"),
        help="Path to config.toml (defaults to ./config.toml)",
    )

    console_parser.add_argument(
        "--direct",
        action="store_true",
        help="(future) Apply directly without tray IPC if tray is unavailable",
    )

    return parser


def handle_console(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2

    binding = config.find_esde_binding(args.name)
    if binding is None:
        print(f"[error] console '{args.name}' not found in ESDE bindings", file=sys.stderr)
        return 3

    try:
        send_console_request(args.name)
    except IPCNotReady as exc:
        # Tray not implemented yet; surface a clear message.
        print(f"[warn] {exc}", file=sys.stderr)
        print(f"[info] binding resolved to: {binding}", file=sys.stderr)
        # Exit non-zero so callers know nothing was applied.
        return 4
    except Exception as exc:  # unexpected IPC failure
        print(f"[error] IPC failure: {exc}", file=sys.stderr)
        return 5

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "console":
        return handle_console(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

