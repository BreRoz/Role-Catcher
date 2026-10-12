"""First-run behavior (section 3d): seed seen_jobs.json silently, or backfill."""
from __future__ import annotations
from .models import Job


def is_first_run(seen: dict) -> bool:
    return not seen


def seed_seen(seen: dict, jobs: list[Job], today: str) -> int:
    """Mark every currently open job as seen. Returns how many were new."""
    added = 0
    for j in jobs:
        if j.key not in seen:
            seen[j.key] = today
            added += 1
    return added


def cap_for_backfill(jobs: list[Job], watchlist: set[str], max_calls: int) -> tuple[list[Job], int]:
    """Hard cap on how many roles a backfill may score. Watchlist companies go
    first, then the rest in a stable order. Returns (kept, skipped_count)."""
    wl = {w.lower() for w in watchlist}
    ordered = sorted(jobs, key=lambda j: (j.company.lower() not in wl, j.company.lower(), j.job_id))
    kept = ordered[:max(0, max_calls)]
    return kept, len(ordered) - len(kept)
