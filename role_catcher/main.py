from __future__ import annotations
import argparse
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import config
from .emailer import send_matches
from .fetchers import fetch_jobs
from .filters import passes_filters
from .scorer import score_job
from .state import load_seen, save_seen


def in_run_window() -> bool:
    """Scheduled runs fire in two UTC slots (to cover daylight saving); only the one
    matching the intended Central-time hour proceeds."""
    hour = datetime.now(ZoneInfo(config.TIMEZONE)).hour
    return hour in config.RUN_HOURS_LOCAL


def run(dry_run: bool = False) -> int:
    seen = load_seen()
    matches = []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for company in config.load_companies():
        try:
            jobs = fetch_jobs(company)
        except Exception as e:  # one broken company must not stop the others
            print(f"[warn] {company['name']}: fetch failed: {e}", file=sys.stderr)
            continue
        for job in jobs:
            if job.key in seen:
                continue
            if not passes_filters(job):
                seen[job.key] = {"first_seen": now, "score": None}
                continue
            score, reason = score_job(job)
            seen[job.key] = {"first_seen": now, "score": score}
            if score >= config.SCORE_THRESHOLD:
                matches.append((job, score, reason))

    print(f"{len(matches)} match(es) at or above {config.SCORE_THRESHOLD}")
    if not dry_run:
        send_matches(matches)
        save_seen(seen)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="don't email or save state")
    p.add_argument("--scheduled", action="store_true", help="skip if outside the Central-time run window")
    args = p.parse_args()
    if args.scheduled and not in_run_window():
        print("Outside scheduled run window; exiting.")
        return 0
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
