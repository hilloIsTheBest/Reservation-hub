from __future__ import annotations
import os
import sys
from pathlib import Path


def _base_dir() -> Path:
    # When bundled by PyInstaller, data is unpacked to sys._MEIPASS
    m = getattr(sys, "_MEIPASS", None)
    if m:
        return Path(m)
    return Path(__file__).resolve().parent.parent


def templates_dir() -> str:
    return str(_base_dir() / "templates")


def static_dir() -> str:
    return str(_base_dir() / "static")

