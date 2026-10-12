import json
from pathlib import Path

STATE_DIR = Path(__file__).resolve().parent.parent / "state"


def load(name: str, default):
    path = STATE_DIR / name
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(name: str, data) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    with open(STATE_DIR / name, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
