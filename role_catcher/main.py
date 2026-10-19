from __future__ import annotations
import argparse
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from . import health, state
from .config import ROOT, load_config, load_yaml
from .discovery import LAST_SUMMARY, discover, mark_baselined
from .catchup import already_emailed_keys, digest_blocks, drop_stale, order_for_cap, posted_within
from .emailer import email_roles, render, send_email
from .filters import prefilter, write_drop_log
from .firstrun import cap_for_backfill, is_first_run, seed_seen
from .scorer import passes_gate, score_jobs


def should_run(config: dict) -> bool:
    """Scheduled runs fire at both daylight and standard UTC offsets; only the
    ones landing on a configured Central hour proceed. Manual runs always go."""
    if os.environ.get("GITHUB_EVENT_NAME") != "schedule":
        return True
    hour = datetime.now(ZoneInfo(config["timezone"])).hour
    return hour in config["run_hours_local"]


def watchlist_names() -> set[str]:
    out = set()
    for item in load_yaml("watchlist.yaml").get("companies") or []:
        out.add((item if isinstance(item, str) else item["name"]).lower())
    return out


def blocked(jobs, blocklist: list) -> list:
    names = {str(n).lower() for n in blocklist or []}
    return [j for j in jobs if j.company.lower() not in names]


def unseen(jobs, seen: dict) -> list:
    """Dedup: drop roles already recorded in seen_jobs.json."""
    return [j for j in jobs if j.key not in seen]


def run_prefilter(jobs: list) -> list:
    """Pre-filter and write every drop, with its reason, to logs/last_run.json."""
    drops: list = []
    kept = prefilter(jobs, load_yaml("filters.yaml"), drops)
    write_drop_log(len(jobs), kept, drops)
    print(f"pre-filter: {len(jobs)} in, {len(kept)} kept, {len(drops)} dropped (see logs/last_run.json)")
    return kept


def backfill(jobs, config: dict, seen: dict, today: str, max_calls: int | None, dry_run: bool) -> None:
    """Score everything currently open (hard-capped) and write logs/backfill.json.
    Sends no email. Marks everything open as seen so the next normal run only
    reports roles posted after this point."""
    cap = max_calls if max_calls is not None else config["backfill"]["max_scoring_calls"]
    config = {**config, "limits": {**config["limits"], "max_roles_scored_per_run": cap}}
    candidates = run_prefilter(jobs)
    to_score, skipped = cap_for_backfill(candidates, watchlist_names(), cap)
    cache = state.load("score_cache.json", {})
    run = score_jobs(to_score, config, cache, watchlist=watchlist_names())
    passed = [s for s in run.scored if passes_gate(s.result, config["scoring"]["threshold"])]
    print(f"backfill: {len(jobs)} open, {len(candidates)} passed pre-filter, {len(to_score)} scored "
          f"(cap {cap}, {skipped} not scored), {len(passed)} passed fit")
    report = {"time": datetime.now().isoformat(timespec="seconds"), "open": len(jobs),
              "prefiltered": len(candidates), "scored": len(to_score), "not_scored_over_cap": skipped,
              "results": [{"score": s.score, "company": s.job.company, "title": s.job.title,
                           "url": s.job.url}
                          for s in passed]}
    if dry_run:
        print("dry run: not writing logs/backfill.json or seen_jobs.json")
        return
    (ROOT / "logs").mkdir(exist_ok=True)
    (ROOT / "logs" / "backfill.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    seed_seen(seen, jobs, today)
    state.save("seen_jobs.json", seen)
    state.save("score_cache.json", cache)
    mark_baselined({j.board for j in jobs})


def catchup(jobs, config: dict, days: int, max_calls: int | None, dry_run: bool) -> None:
    """One-off: score roles posted in the last `days` days (undated roles are skipped)
    and send a single digest email. Leaves seen_jobs.json alone."""
    cap = max_calls if max_calls is not None else config["backfill"]["max_scoring_calls"]
    config = {**config, "limits": {**config["limits"], "max_roles_scored_per_run": cap}}
    days = min(days, config["max_posting_age_days"])
    recent = posted_within(jobs, days)
    emailed = already_emailed_keys()
    recent = [j for j in recent if j.key not in emailed]
    candidates = run_prefilter(recent)
    to_score = order_for_cap(candidates, watchlist_names())[:cap]
    cache = state.load("score_cache.json", {})
    sr = score_jobs(to_score, config, cache, watchlist=watchlist_names())
    if sr.failures and not sr.scored:
        raise SystemExit(f"CATCHUP FAILED: all {sr.failures} scoring calls failed (see errors above); no email sent.")
    passed = [s for s in sr.scored if passes_gate(s.result, config["scoring"]["threshold"])]
    stats = (f"{len(recent)} dated roles posted in the last {days} days, {len(candidates)} passed the "
             f"pre-filter, {len(sr.scored)} scored (cap {cap}, {len(candidates) - len(sr.scored)} not scored), "
             f"{len(passed)} passed fit.")
    print("catchup:", stats)
    text, html_body = render(digest_blocks(passed, days, stats))
    subject = f"[Role Catcher] Catch-up: {len(passed)} roles from the last {days} days"
    if dry_run:
        print(f"--- would send ---\nSubject: {subject}\n\n{text}")
        return
    send_email(subject, text, config, html_body)
    state.save("score_cache.json", cache)
    print(f"emailed: {subject}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="role_catcher")
    ap.add_argument("--dry-run", action="store_true", help="do everything except send email and save state")
    ap.add_argument("--backfill", action="store_true",
                    help="score everything currently open (capped), no email, then mark it all seen")
    ap.add_argument("--catchup", action="store_true",
                    help="one-off: score roles posted in the last --days days and email one digest")
    ap.add_argument("--days", type=int, default=7, help="look-back window for --catchup")
    ap.add_argument("--max-calls", type=int, help="override the backfill scoring-call cap")
    args = ap.parse_args()

    config = load_config()
    if not should_run(config):
        print("Not a configured run hour; exiting.")
        return

    stats: dict = {}
    try:
        pipeline(args, config, stats)
    except Exception as e:      # recorded so the weekly email can show the crash, then re-raised (red run on GitHub)
        stats["fatal_error"] = f"{type(e).__name__}: {e}"
        raise
    finally:
        if not args.dry_run and not args.backfill and not args.catchup:
            health.record_run(stats)
        if not args.backfill and not args.catchup:
            health.maybe_send_weekly(config, dry_run=args.dry_run)
    if stats.get("discovery", {}).get("boards_tried") and not stats["discovery"].get("boards_ok"):
        raise SystemExit("DISCOVERY FAILED: no job board answered. Failing the run so it shows red.")


def pipeline(args, config: dict, stats: dict) -> None:
    today = datetime.now().date().isoformat()
    seen = state.load("seen_jobs.json", {})
    jobs = discover(config, save=not args.dry_run)
    jobs = blocked(jobs, load_yaml("blocklist.yaml").get("companies"))
    stats["discovery"] = {k: LAST_SUMMARY.get(k) for k in ("boards_tried", "boards_ok", "jobs_seen", "by_ats")}
    stats["caps_hit"] = []

    if args.catchup:
        catchup(jobs, config, args.days, args.max_calls, args.dry_run)
        return
    if args.backfill:
        backfill(jobs, config, seen, today, args.max_calls, args.dry_run)
        return

    # Jobs already open on a board we have not seen before are baseline, not news:
    # seed them silently. On the very first run that is every job.
    first_run = is_first_run(seen)
    baseline = [j for j in jobs if j.new_board]
    if first_run and not jobs:
        print("First run, but discovery returned no jobs; not seeding (will retry next run).")
        return
    if baseline:
        if args.dry_run:
            print(f"{'First run: ' if first_run else ''}would seed {len(baseline)} open roles as seen, no email.")
        else:
            n = seed_seen(seen, baseline, today)
            state.save("seen_jobs.json", seen)
            mark_baselined({j.board for j in baseline})
            print(f"{'First run: ' if first_run else ''}seeded {n} open roles as seen. No email sent.")
        jobs = [j for j in jobs if not j.new_board]
    if first_run:
        return

    jobs = drop_stale(unseen(jobs, seen), config["max_posting_age_days"])
    stats["new_roles"] = len(jobs)
    n_in = len(jobs)
    jobs = run_prefilter(jobs)
    stats["prefilter"] = {"in": n_in, "kept": len(jobs), "dropped": n_in - len(jobs)}
    cache = state.load("score_cache.json", {})
    sr = score_jobs(jobs, config, cache, watchlist=watchlist_names())
    scored = [s for s in sr.scored if passes_gate(s.result, config["scoring"]["threshold"])]
    stats["scoring"] = {"calls": sr.calls, "cache_hits": sr.cache_hits, "failures": sr.failures,
                        "passed": len(scored), "cost": round(sr.cost, 4)}
    if sr.cap_hit:
        stats["caps_hit"].append(sr.cap_reason)

    if args.dry_run:
        email_roles(scored, config, dry_run=True)
        print("dry run: no email sent, state not saved.")
        return

    # One email per role, sent now (not a digest). Only roles whose email went out
    # (or that were rejected) are marked seen; failed or capped sends retry next run.
    er = email_roles(scored, config)
    stats["emails"] = {"sent": len(er.sent), "failed": len(er.failed), "capped": len(er.capped)}
    if er.cap_hit:
        stats["caps_hit"].append(f"emails ({config['limits']['max_emails_per_day']} per day)")

    retry = {s.job.key for s in er.failed + er.capped}
    for s in sr.scored:
        if s.job.key not in retry:
            seen[s.job.key] = today
    state.save("seen_jobs.json", seen)
    state.save("score_cache.json", cache)


if __name__ == "__main__":
    main()
