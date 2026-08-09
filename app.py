"""Vercel entry point for the secure API behind the GitHub Pages frontend."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from agentic_digital_twin.main import app  # noqa: E402, F401
