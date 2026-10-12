"""Step 1: collect open roles from Greenhouse, Ashby, Lever (section 3).

Watchlist boards are fetched every run; the wider registry of harvested boards
is swept once per `discovery.full_sweep_hours`. Board lists come from
`slugs.py`; fetching and normalizing from `ats.py`.
"""
from __future__ import annotations
import json
import logging
from datetime import datetime, timedelta, timezone

from . import ats, state
from .models import Job
from .slugs import board_list, load_registry

log = logging.getLogger("role_catcher.discovery")

LAST_SUMMARY: dict = {}   # the most recent discover() summary, read by the run log and weekly health email


def by_ats(boards: list[dict], errors: list[dict], counts: dict) -> dict:
    """Per-source (ATS) tallies: boards tried, boards that answered, errors, open roles seen."""
    def row(ats):
        return out.setdefault(ats, {"tried": 0, "ok": 0, "errors": 0, "jobs": 0})
    out: dict = {}
    for b in boards:
        row(b["ats"])["tried"] += 1
    for key, n in counts.items():
        r = row(key.split(":", 1)[0])
        r["ok"] += 1
        r["jobs"] += n
    for e in errors:
        row(e["ats"])["errors"] += 1
    return out


def sweep_due(reg: dict, hours: float, now: datetime | None = None) -> bool:
    last = reg.get("last_full_sweep")
    if not last:
        return True
    now = now or datetime.now(timezone.utc)
    return now - datetime.fromisoformat(last) >= timedelta(hours=hours)


def discover(config: dict, keep=None, save: bool = True) -> list[Job]:
    """Returns normalized open postings. Never raises on a bad board: failures
    are logged and written to logs/discovery.json (zero boards or many errors
    is what the weekly health email reports)."""
    cfg = config.get("discovery", {})
    reg = load_registry()
    full = sweep_due(reg, cfg.get("full_sweep_hours", 24))
    boards = board_list(reg) if full else board_list(reg, sources=("watchlist",))
    jobs, errors, counts = ats.fetch_boards(
        boards, workers=cfg.get("workers", 6), pause=cfg.get("pause_seconds", 0.2), keep=keep)

    now = datetime.now(timezone.utc)
    for j in jobs:
        j.new_board = not reg["boards"].get(j.board, {}).get("baselined")
    for key, n in counts.items():
        if n == 0:
            reg["boards"][key]["baselined"] = True  # nothing open yet, so nothing to hide
        reg["boards"][key]["jobs"] = n
        reg["boards"][key]["checked"] = now.date().isoformat()
    for e in errors:
        key = f"{e['ats']}:{e['slug']}"
        if e["kind"] == "not_found" and key in reg["boards"] and reg["boards"][key]["source"] != "watchlist":
            reg["boards"][key]["status"] = "dead"  # harvested board that vanished; watchlist boards are kept
    if full:
        reg["last_full_sweep"] = now.isoformat()
    if save:
        state.save("board_registry.json", reg)

    summary = {"time": now.isoformat(), "full_sweep": full, "boards_tried": len(boards),
               "boards_ok": len(counts), "jobs_seen": sum(counts.values()), "jobs_kept": len(jobs),
               "by_ats": by_ats(boards, errors, counts),
               "errors": errors, "watchlist_unresolved": reg.get("unresolved", [])}
    LAST_SUMMARY.clear()
    LAST_SUMMARY.update(summary)
    if save:
        (state.STATE_DIR.parent / "logs").mkdir(exist_ok=True)
        with open(state.STATE_DIR.parent / "logs" / "discovery.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
    log.info("discovery: %d/%d boards ok, %d jobs seen", len(counts), len(boards), summary["jobs_seen"])
    if not counts:
        log.error("DISCOVERY RETURNED ZERO BOARDS")
    return jobs


def mark_baselined(board_keys: set[str]) -> None:
    """Call after a board's current jobs have been recorded as seen, so later
    runs treat only newly posted jobs on it as new."""
    if not board_keys:
        return
    reg = load_registry()
    for k in board_keys:
        if k in reg["boards"]:
            reg["boards"][k]["baselined"] = True
    state.save("board_registry.json", reg)
