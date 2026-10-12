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


def _today_records(tz_name: str, purpose: str | None, path: Path | None):
    path = path or LOG_PATH      # looked up at call time so tests can point it elsewhere
    if not path.exists():
        return
    tz = ZoneInfo(tz_name)
    today = datetime.now(tz).date()
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
                when = datetime.fromisoformat(rec["ts"]).astimezone(tz).date()
            except (ValueError, KeyError):
                continue
            if when == today and (purpose is None or rec.get("purpose") == purpose):
                yield rec


def spend_today(tz_name: str, purpose: str | None = None, path: Path | None = None) -> float:
    """Total real USD cost logged today (in the configured timezone).
    Pass purpose="scoring" or "discovery" for a per-step total."""
    return sum(float(r.get("cost") or 0.0) for r in _today_records(tz_name, purpose, path))


def calls_today(tz_name: str, purpose: str, path: Path | None = None) -> int:
    """Model calls made today for a purpose. A JSON-repair retry (attempt 2) is
    part of the same call, so only first attempts are counted."""
    return sum(1 for r in _today_records(tz_name, purpose, path) if r.get("attempt", 1) == 1)


def spend_by_purpose(since: datetime, path: Path = LOG_PATH) -> dict[str, float]:
    """Real USD cost since `since` (timezone-aware), grouped by purpose."""
    out: dict[str, float] = {}
    if not path.exists():
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
                when = datetime.fromisoformat(rec["ts"])
            except (ValueError, KeyError):
                continue
            if when >= since:
                purpose = rec.get("purpose", "other")
                out[purpose] = out.get(purpose, 0.0) + float(rec.get("cost") or 0.0)
    return out
