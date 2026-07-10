"""Path setup for spotify unit tests — mirrors tests/unit/python/conftest.py
so ``from lib.digit_playlists import ...`` and
``from sources.spotify.fetch import ...`` resolve against services/."""

import sys
from pathlib import Path

SERVICES_DIR = Path(__file__).resolve().parents[3] / "services"
sys.path.insert(0, str(SERVICES_DIR))
