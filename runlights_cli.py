from __future__ import annotations

import sys
from pathlib import Path


# Add local src/ to import path when running directly without installation.
here = Path(__file__).resolve().parent
sys.path.insert(0, str(here / "src"))

from runlights.cli import main  # noqa: E402  (import after path tweak)


if __name__ == "__main__":
    raise SystemExit(main())

