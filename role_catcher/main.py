from __future__ import annotations
import argparse
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from . import state
from .company_vetting import vet_companies
from .config import ROOT, load_config, load_yaml
from .discovery import discover, mark_baselined
from .emailer import email_roles
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


def run_prefilter(jobs: list) -> list:
    """Pre-filter and write every drop, with its reason, to logs/last_run.json."""
    drops: list = []
    kept = prefilter(jobs, load_yaml("filters.yaml"), drops)
    write_drop_log(len(jobs), kept, drops)
    print(f"pre-filter: {len(jobs)} in, {len(kept)} kept, {len(drops)} dropped (see logs/last_run.json)")
    return kept


def log_blocked(blocked: list) -> None:
    """Append blocked roles and the reason to logs/blocked.jsonl so a wrong block can be overruled."""
    if not blocked:
        return
    (ROOT / "logs").mkdir(exist_ok=True)
    with open(ROOT / "logs" / "blocked.jsonl", "a", encoding="utf-8") as f:
        for s in blocked:
            f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "company": s.job.company,
                                "title": s.job.title, "url": s.job.url, "score": s.score,
                                "reasons": s.company_verdict.get("reasons", []),
                                "sources": s.company_verdict.get("sources", [])}) + "\n")


def backfill(jobs, config: dict, seen: dict, today: str, max_calls: int | None, dry_run: bool) -> None:
    """Score everything currently open (hard-capped) and write logs/backfill.json.
    Sends no email. Marks everything open as seen so the next normal run only
    reports roles posted after this point."""
    cap = max_calls if max_calls is not None else config["backfill"]["max_scoring_calls"]
    config = {**config, "limits": {**config["limits"], "max_roles_scored_per_run": cap,
                                   "max_companies_vetted_per_run": config["backfill"]["max_vetting_calls"]}}
    candidates = run_prefilter(jobs)
    to_score, skipped = cap_for_backfill(candidates, watchlist_names(), cap)
    cache = state.load("score_cache.json", {})
    run = score_jobs(to_score, config, cache, watchlist=watchlist_names())
    passed = [s for s in run.scored if passes_gate(s.result, config["scoring"]["threshold"])]
    verdicts = state.load("company_verdicts.json", {})
    vet = vet_companies(passed, config, verdicts, watchlist_names())
    approved = vet.approved
    print(f"backfill: {len(jobs)} open, {len(candidates)} passed pre-filter, {len(to_score)} scored "
          f"(cap {cap}, {skipped} not scored), {len(passed)} passed fit, {len(approved)} passed vetting, "
          f"{len(vet.blocked)} blocked")
    report = {"time": datetime.now().isoformat(timespec="seconds"), "open": len(jobs),
              "prefiltered": len(candidates), "scored": len(to_score), "not_scored_over_cap": skipped,
              "results": [{"score": s.score, "company": s.job.company, "title": s.job.title,
                           "url": s.job.url, "verdict": s.company_verdict}
                          for s in approved + vet.blocked]}
    if dry_run:
        print("dry run: not writing logs/backfill.json or seen_jobs.json")
        return
    (ROOT / "logs").mkdir(exist_ok=True)
    (ROOT / "logs" / "backfill.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    seed_seen(seen, jobs, today)
    state.save("seen_jobs.json", seen)
    state.save("score_cache.json", cache)
    state.save("company_verdicts.json", verdicts)
    mark_baselined({j.board for j in jobs})


def main() -> None:
    ap = argparse.ArgumentParser(prog="role_catcher")
    ap.add_argument("--dry-run", action="store_true", help="do everything except send email and save state")
    ap.add_argument("--backfill", action="store_true",
                    help="score everything currently open (capped), no email, then mark it all seen")
    ap.add_argument("--max-calls", type=int, help="override the backfill scoring-call cap")
    args = ap.parse_args()

    config = load_config()
    if not should_run(config):
        print("Not a configured run hour; exiting.")
        return

    today = datetime.now().date().isoformat()
    seen = state.load("seen_jobs.json", {})
    jobs = discover(config, save=not args.dry_run)
    jobs = blocked(jobs, load_yaml("blocklist.yaml").get("companies"))

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

    jobs = [j for j in jobs if j.key not in seen]
    jobs = run_prefilter(jobs)
    cache = state.load("score_cache.json", {})
    run = score_jobs(jobs, config, cache, watchlist=watchlist_names())
    scored = [s for s in run.scored if passes_gate(s.result, config["scoring"]["threshold"])]
    verdicts = state.load("company_verdicts.json", {})
    vet = vet_companies(scored, config, verdicts, watchlist_names())
    approved = vet.approved
    if args.dry_run:
        for r in approved:
            print(f"would email: {r.score}% {r.job.title} | {r.job.company} | "
                  f"{r.company_verdict.get('verdict')} | {r.job.url}")
        for r in vet.blocked:
            print(f"would block: {r.score}% {r.job.title} | {r.job.company} | "
                  f"{'; '.join(r.company_verdict.get('reasons', []))}")
        print("dry run: no email sent, state not saved.")
        return
    email_roles(approved, config)

    log_blocked(vet.blocked)
    deferred = {s.job.key for s in vet.deferred}
    for s in run.scored:  # remember what was evaluated; deferred or failed roles retry next run
        if s.job.key not in deferred:
            seen[s.job.key] = today
    state.save("seen_jobs.json", seen)
    state.save("score_cache.json", cache)
    state.save("company_verdicts.json", verdicts)


if __name__ == "__main__":
    main()
