from __future__ import annotations

from pathlib import Path

# Allow running without installation by adjusting path.
import sys
from pathlib import Path as _Path
_here = _Path(__file__).resolve().parent
sys.path.insert(0, str(_here / "src"))

from runlights.tray import serve  # noqa: E402


if __name__ == "__main__":
    cfg = Path("config.toml")
    serve(config_path=cfg)

