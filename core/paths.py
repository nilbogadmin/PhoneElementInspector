"""Where the app reads bundled files and where it writes saved rules."""
from __future__ import annotations

import sys
from pathlib import Path


def app_dir() -> Path:
    """Folder next to the exe when installed, otherwise the project root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """Bundled demo files. Inside the exe these live in the PyInstaller temp dir."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", app_dir()))
    return app_dir()
