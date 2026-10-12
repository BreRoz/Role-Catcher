"""Per-call cost log (logs/spend.jsonl), fed by the real cost OpenRouter returns."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "spend.jsonl"


def log_call(record: dict, path: Path = LOG_PATH) -> None:
    path.parent.mkdir(exist_ok=True)
    record = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **record}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def spend_today(tz_name: str, purpose: str | None = None, path: Path = LOG_PATH) -> float:
    """Total real USD cost logged today (in the configured timezone).
    Pass purpose="scoring" or "vetting" for a per-step total."""
    if not path.exists():
        return 0.0
    tz = ZoneInfo(tz_name)
    today = datetime.now(tz).date()
    total = 0.0
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
                when = datetime.fromisoformat(rec["ts"]).astimezone(tz).date()
            except (ValueError, KeyError):
                continue
            if when == today and (purpose is None or rec.get("purpose") == purpose):
                total += float(rec.get("cost") or 0.0)
    return total
