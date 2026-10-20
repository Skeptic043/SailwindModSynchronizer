"""Imports of the catalog maintenance scripts in scripts/ for tests."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"


def load_script(name: str) -> ModuleType:
    """Import the script ``scripts/<name>.py`` under its own module name, as the scripts import each other."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    return importlib.import_module(name)
