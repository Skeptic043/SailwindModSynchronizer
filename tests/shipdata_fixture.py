"""Authored per-ship override files shaped like Better Drag's shipdata JSON."""
from __future__ import annotations

import json

SHIPDATA = {"BOAT BRIG (10)": {"drag": 1.0}, "BOAT JUNK (20)": {"drag": 2.0}}
# The player tuned a shipped example.
EDITED_SHIPDATA = {**SHIPDATA, "BOAT BRIG (10)": {"drag": 1.5}}
# The next mod version ships one more ship.
GALLUS = {"BOAT GALLUS (197)": {"drag": 3.0}}


def ship_json(value: dict, indent: int = 2) -> bytes:
    return (json.dumps(value, indent=indent) + "\n").encode("utf-8")
