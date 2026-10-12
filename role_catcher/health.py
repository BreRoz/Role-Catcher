"""Run log and the Monday health email (section 7).

Every real run appends one line to logs/runs.jsonl. The weekly email is built
from that log plus logs/spend.jsonl. Silent failure is the
main risk, so anything wrong goes in a PROBLEMS list at the very top."""
from __future__ import annotations
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import emailer, spend, state
from .config import ROOT

log = logging.getLogger("role_catcher.health")

RUNS_LOG = ROOT / "logs" / "runs.jsonl"
SOURCES = ("greenhouse", "ashby", "lever")
SOURCE_ALARM_DAYS = 14


def record_run(stats: dict, path: Path = RUNS_LOG) -> None:
    path.parent.mkdir(exist_ok=True)
    rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **stats}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
            rec["_when"] = datetime.fromisoformat(rec["ts"]).astimezone(timezone.utc)
            out.append(rec)
        except (ValueError, KeyError):
            continue
    return out


def since(recs: list[dict], cutoff: datetime) -> list[dict]:
    return [r for r in recs if r["_when"] >= cutoff]


def source_problems(runs: list[dict], now: datetime, alarm_days: int = SOURCE_ALARM_DAYS) -> list[str]:
    """A discovery source (an ATS) that returned zero open roles in every run of
    the last `alarm_days` days, or that is erroring on every board, is a problem.
    With less history than that, the same pattern is reported as an early warning."""
    problems = []
    window = since(runs, now - timedelta(days=alarm_days))
    if not window:
        return problems
    span = (now - min(r["_when"] for r in window)).days
    for src in SOURCES:
        rows = [r["discovery"]["by_ats"][src] for r in window
                if src in (r.get("discovery") or {}).get("by_ats", {}) and r["discovery"]["by_ats"][src]["tried"]]
        if not rows or sum(x["jobs"] for x in rows) > 0:
            continue
        errors = sum(x["errors"] for x in rows)
        how = f"errored on {errors} board checks and returned zero roles" if errors else "returned zero roles"
        if span >= alarm_days - 1:
            problems.append(f"Discovery source {src} {how} in every run for {alarm_days}+ days.")
        else:
            problems.append(f"Early warning: discovery source {src} {how} in every run so far "
                            f"({span} days of history; it becomes an alarm at {alarm_days}).")
    return problems


def _sum(runs: list[dict], *path) -> int:
    total = 0
    for r in runs:
        v = r
        for p in path:
            v = (v or {}).get(p)
        total += v or 0
    return total


def build_weekly(config: dict, now: datetime | None = None, runs_path: Path = RUNS_LOG,
                 spend_path: Path = spend.LOG_PATH,
                 registry: dict | None = None) -> tuple[str, str, str]:
    now = now or datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    all_runs = read_jsonl(runs_path)
    runs = since(all_runs, week_ago)
    costs = spend.spend_by_purpose(week_ago, spend_path)
    tz = ZoneInfo(config["timezone"])

    problems: list[str] = []
    scheduled = len(config.get("run_hours_local", [])) * 7
    if not runs:
        problems.append("No runs were recorded this week. The schedule may have stopped "
                        "(GitHub pauses scheduled workflows after 60 days without repo activity).")
    elif scheduled and len(runs) < scheduled * 0.6:
        problems.append(f"Only {len(runs)} runs completed this week, expected about {scheduled}.")
    for r in runs:
        if r.get("fatal_error"):
            problems.append(f"Run on {r['_when'].astimezone(tz):%a %b %d %H:%M} crashed: {r['fatal_error']}")
        if (r.get("discovery") or {}).get("boards_ok") == 0 and (r.get("discovery") or {}).get("boards_tried"):
            problems.append(f"Discovery reached zero boards on {r['_when'].astimezone(tz):%a %b %d %H:%M}.")
    problems += source_problems(all_runs, now)
    caps = sorted({c for r in runs for c in r.get("caps_hit", [])})
    for c in caps:
        problems.append(f"Daily cap hit this week: {c}. Affected roles were deferred, not lost.")
    for label, path in (("Role scoring calls failed", ("scoring", "failures")),
                        ("Email sends failed", ("emails", "failed"))):
        n = _sum(runs, *path)
        if n:
            problems.append(f"{label}: {n} this week.")

    reg = registry if registry is not None else state.load("board_registry.json", {"boards": {}})
    live = [b for b in reg.get("boards", {}).values() if b.get("status") == "live"]
    companies = len({b.get("company", "").lower() for b in live})
    by_src = {s: sum(1 for k, b in reg.get("boards", {}).items()
                     if b.get("status") == "live" and k.startswith(s + ":")) for s in SOURCES}

    status = "OK" if not problems else f"ATTENTION ({len(problems)})"
    subject = f"[Role Catcher] Weekly health | {status}"
    blocks: list = [("title", f"Weekly health, week ending {now.astimezone(tz):%b} {now.astimezone(tz).day}")]
    if problems:
        blocks += [("warn", f"{len(problems)} thing(s) need your attention:"), ("bullets", problems)]
    else:
        blocks.append(("p", "No problems detected this week."))

    blocks += [("h", "Discovery coverage"),
               ("kv", [("Boards covered", f"{len(live)} ({', '.join(f'{n} {s}' for s, n in by_src.items())})"),
                       ("Companies covered", str(companies)),
                       ("Runs this week", f"{len(runs)} of about {scheduled}")]),
               ("h", "Funnel this week"),
               ("kv", [("Open roles seen", str(_sum(runs, "discovery", "jobs_seen")) + " (counted each run)"),
                       ("New roles", str(_sum(runs, "new_roles"))),
                       ("Pre-filtered out", str(_sum(runs, "prefilter", "dropped"))),
                       ("Roles scored", f"{_sum(runs, 'scoring', 'calls')} model calls, "
                                        f"{_sum(runs, 'scoring', 'cache_hits')} from cache"),
                       ("Passed role fit", str(_sum(runs, "scoring", "passed"))),
                       ("Roles emailed", str(_sum(runs, "emails", "sent")))])]

    blocks.append(("h", "Discovery sources, this week"))
    rows = []
    for src in SOURCES:
        tried = sum(r["discovery"]["by_ats"].get(src, {}).get("tried", 0) for r in runs if r.get("discovery"))
        errs = sum(r["discovery"]["by_ats"].get(src, {}).get("errors", 0) for r in runs if r.get("discovery"))
        jobs = sum(r["discovery"]["by_ats"].get(src, {}).get("jobs", 0) for r in runs if r.get("discovery"))
        rows.append((src, f"{jobs} roles seen, {errs} errors across {tried} board checks"))
    blocks.append(("kv", rows))

    total = sum(costs.values())
    other = total - costs.get("scoring", 0) - costs.get("discovery", 0)
    spend_rows = [("Scoring", f"${costs.get('scoring', 0):.2f}"),
                  ("Discovery", f"${costs.get('discovery', 0):.2f}")]
    if other > 0.005:
        spend_rows.append(("Other (calibration, manual tests)", f"${other:.2f}"))
    spend_rows.append(("Total OpenRouter spend", f"${total:.2f}"))
    blocks += [("h", "OpenRouter spend, last 7 days"), ("kv", spend_rows)]
    text, html_body = emailer.render(blocks)
    return subject, text, html_body


def weekly_due(config: dict, now: datetime | None = None, hs: dict | None = None) -> bool:
    """Monday (Central), once per day, from the first run of the day."""
    now = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(config["timezone"]))
    hs = hs if hs is not None else state.load("health_state.json", {})
    return now.weekday() == 0 and hs.get("last_weekly") != now.date().isoformat()


def maybe_send_weekly(config: dict, dry_run: bool = False, sender=None) -> bool:
    """Send the weekly email if due. Never raises; returns True if sent (or printed)."""
    try:
        if not weekly_due(config):
            return False
        subject, text, html_body = build_weekly(config)
        if dry_run:
            print(f"--- would send weekly health email ---\nSubject: {subject}\n\n{text}")
            return True
        (sender or emailer.send_email)(subject, text, config, html_body)
        today = datetime.now(ZoneInfo(config["timezone"])).date().isoformat()
        state.save("health_state.json", {**state.load("health_state.json", {}), "last_weekly": today})
        print(f"emailed: {subject}")
        return True
    except Exception as e:
        log.error("WEEKLY HEALTH EMAIL FAILED: %s", e)
        print(f"WEEKLY HEALTH EMAIL FAILED: {e} (will retry on the next run today)")
        return False
