from __future__ import annotations
import json

from .config import STATE_FILE


def load_seen() -> dict:
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text() or "{}")


def save_seen(seen: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(seen, indent=2, sort_keys=True) + "\n")
