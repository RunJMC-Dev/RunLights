from __future__ import annotations

import argparse
import sys

from .ipc import IPCNotReady, send_console_request


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="runlights-cli", description="RunLights command-line interface")
    parser.add_argument("name", help="Console name (e.g., snes, megadrive)")
    return parser


def handle_console(args: argparse.Namespace) -> int:
    try:
        send_console_request(args.name)
    except IPCNotReady as exc:
        print(f"[warn] {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # unexpected IPC failure
        print(f"[error] IPC failure: {exc}", file=sys.stderr)
        return 3

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    return handle_console(args)


if __name__ == "__main__":
    raise SystemExit(main())
