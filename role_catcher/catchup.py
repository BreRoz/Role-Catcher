"""One-off catch-up: score roles posted in the last N days and send one digest
email. Does not touch seen_jobs.json, so normal runs are unaffected."""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone

from .config import ROOT
from .models import Job, ScoredJob


def _parse(iso: str | None) -> datetime | None:
    try:
        d = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def posted_within(jobs: list[Job], days: int, now: datetime | None = None) -> list[Job]:
    """Jobs with a posting date no older than `days`. Undated jobs are excluded."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    out = []
    for j in jobs:
        d = _parse(j.posted_at)
        if d and d >= cutoff:
            out.append(j)
    return out


def already_emailed_keys(path=None) -> set[str]:
    path = path or ROOT / "logs" / "emails.jsonl"
    if not path.exists():
        return set()
    keys = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            keys.add(json.loads(line)["key"])
        except (ValueError, KeyError):
            continue
    return keys


def order_for_cap(jobs: list[Job], watchlist: set[str]) -> list[Job]:
    """Watchlist companies first, then newest posted first."""
    wl = {w.lower() for w in watchlist}
    return sorted(jobs, key=lambda j: (j.company.lower() not in wl,
                                       -(_parse(j.posted_at) or datetime.min.replace(tzinfo=timezone.utc)).timestamp(),
                                       j.key))


def digest_blocks(scored: list[ScoredJob], days: int, stats: str) -> list:
    blocks: list = [("title", f"Roles from the last {days} days you may have missed"), ("p", stats)]
    for s in sorted(scored, key=lambda r: -r.score):
        j = s.job
        label = f"{s.score}% | {j.title} | {j.company} | {j.location or 'location not listed'}"
        blocks.append(("bullets", [(label, j.url)]))
    if not scored:
        blocks.append(("p", "No roles passed the fit threshold."))
    return blocks
