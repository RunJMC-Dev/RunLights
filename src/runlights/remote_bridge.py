from __future__ import annotations

import argparse
import logging
import signal
import threading
from pathlib import Path

from .config import load_config
from .remote.config import load_remote_bridge_config
from .remote.engine import RemoteBridgeEngine


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RunLights device-specific remote bridge")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--parent-pid", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.state_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("Runlights.RemoteBridge")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(args.state_dir / "remote-bridge.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    raw = load_config(args.config).raw
    config = load_remote_bridge_config(raw)
    if not config.enabled:
        logger.info("bridge disabled in configuration")
        return 0
    stop_event = threading.Event()

    def stop(_signum=None, _frame=None):
        stop_event.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    engine = RemoteBridgeEngine(
        config, args.state_dir, logger.info, stop_event, parent_pid=args.parent_pid
    )
    try:
        engine.run()
    except Exception:
        logger.exception("bridge stopped unexpectedly")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
