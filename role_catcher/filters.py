"""Step 2: title, seniority, location, comp pre-filter in code, no AI (section 4).

All rules live in filters.yaml. `evaluate` decides one job and says why;
`prefilter` applies it to a list and can collect every drop for the audit log.
When a signal is ambiguous the job is kept: it is cheaper to score a few extra
roles than to miss a good one.
"""
from __future__ import annotations
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import ats
from .models import Job

LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "last_run.json"


def norm(text: str) -> str:
    """Lowercase, '&' to 'and', punctuation to spaces: 'Go-to-Market' -> 'go to market'."""
    t = (text or "").lower().replace("&", " and ")
    t = t.replace("u.s.a.", " usa ").replace("u.s.", " us ")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", t)).strip()


def _pat(phrase: str) -> str:
    return rf"(?<![a-z0-9]){re.escape(norm(phrase))}(?![a-z0-9])"


def has_phrase(text: str, phrase: str) -> bool:
    """Whole-word match of a phrase in already-normalized text."""
    return re.search(_pat(phrase), text) is not None


def first_match(text: str, phrases) -> str | None:
    for p in phrases or []:
        if has_phrase(text, p):
            return p
    return None


# ---- title ----------------------------------------------------------------

def title_verdict(job: Job, rules: dict) -> str | None:
    """None if the title passes, else the reason it is dropped."""
    title = norm(job.title)
    hit = first_match(title, rules.get("title_include"))
    if not hit:
        for pair in rules.get("title_include_paired") or []:
            if has_phrase(title, pair["match"]) and first_match(title, pair["with"]):
                hit = pair["match"]
                break
    if not hit:
        return "title: no include pattern matched"

    exceptions = rules.get("title_exclude_exceptions") or {}
    unless_jd = rules.get("title_exclude_unless_description") or {}
    desc = norm(job.description)
    for term in rules.get("title_exclude") or []:
        if not has_phrase(title, term):
            continue
        if first_match(title, exceptions.get(term)):
            continue
        jd = unless_jd.get(term)
        if jd and sum(len(re.findall(rf"(?<![a-z0-9]){re.escape(norm(t))}", desc))
                      for t in jd["terms"]) >= jd["min_hits"]:
            continue
        return f"title: excluded by '{term}'"

    level = first_match(title, rules.get("seniority_exclude"))
    if level:
        return f"seniority: '{level}' is below Manager"
    return None


# ---- location -------------------------------------------------------------

REMOTE_WORDS = ("remote", "work from home", "distributed")


def _places(location: str) -> list[str]:
    parts = re.split(r"[|;•·]", location or "")
    return [p for p in (norm(x) for x in parts) if p]


def _strip_non_us(text: str, loc_rules: dict) -> tuple[str, bool]:
    """Remove non-US place names so 'Latin America' does not count as 'America'."""
    found = False
    for m in sorted(loc_rules.get("non_us_markers") or [], key=len, reverse=True):
        if has_phrase(text, m):
            found = True
            text = re.sub(_pat(m), " ", text)
    return text, found


def _non_us(part: str, loc_rules: dict) -> bool:
    """True if the text names a non-US place and nothing US."""
    rest, found = _strip_non_us(part, loc_rules)
    return found and first_match(rest, loc_rules.get("us_markers")) is None


def _bare(part: str, loc_rules: dict) -> str:
    """The part with remote/US words removed: empty means it names no place."""
    rest = part
    for w in list(REMOTE_WORDS) + list(loc_rules.get("us_markers") or []):
        rest = re.sub(_pat(w), " ", rest)
    return rest.strip()


def _remote_us(parts: list[str], loc_rules: dict) -> bool:
    """Remote and open to US candidates. A bare 'Remote' next to only non-US
    places ('Tokyo | Remote') belongs to those places, so it does not count."""
    remote = [p for p in parts if any(w in p for w in REMOTE_WORDS)]
    if not remote:
        return False
    others = [p for p in parts if p not in remote]
    for p in remote:
        if _bare(p, loc_rules) == "" and first_match(p, loc_rules.get("us_markers")) is None:
            if not (others and all(_non_us(o, loc_rules) for o in others)):
                return True
        elif not _non_us(p, loc_rules):
            return True
    return False


def location_class(location: str, loc_rules: dict) -> str:
    """One of: remote, austin, nyc, ambiguous, other."""
    parts = _places(location)
    if not parts:
        return "ambiguous"
    if loc_rules.get("allow_remote", True) and _remote_us(parts, loc_rules):
        return "remote"
    places = loc_rules.get("allowed_places") or {}
    if any(first_match(p, places.get("austin")) for p in parts):
        return "austin"
    if any(first_match(p, loc_rules.get("us_markers")) and not _bare(p, loc_rules) for p in parts):
        return "ambiguous"  # a bare "United States" next to cities usually means nationwide
    if any(first_match(p, places.get("nyc")) for p in parts):
        return "nyc"
    if any(first_match(p, loc_rules.get("ambiguous")) and not _non_us(p, loc_rules) for p in parts):
        return "ambiguous"
    return "other"


# ---- comp -----------------------------------------------------------------

def comp_check(job: Job, floor: float) -> bool | None:
    """True if any listed pay range starts at or above the floor, False if
    ranges are listed and none do, None if no usable range is listed."""
    ranges = ([job.comp] if job.comp else []) + ats.find_pay_ranges(job.description)
    results = [r for r in (ats.comp_meets_floor(c, floor) for c in ranges) if r is not None]
    return None if not results else any(results)


# ---- decision -------------------------------------------------------------

def evaluate(job: Job, rules: dict) -> tuple[str | None, list[str]]:
    """(drop_reason, flags). drop_reason is None when the job is kept."""
    reason = title_verdict(job, rules)
    if reason:
        return reason, []

    flags: list[str] = []
    kind = location_class(job.location, rules.get("location") or {})
    comp = rules["comp"]
    shown = job.comp or "pay band in description"
    if kind == "remote" and _non_us(norm(job.title), rules.get("location") or {}):
        return f"location: remote, but the title names a non-US region ({job.title})", []
    if kind == "other":
        return f"location: not remote US, Austin, or NYC ({job.location})", []
    if kind == "ambiguous":
        flags.append("check location")
    elif kind in ("remote", "austin"):
        floor = comp["min_annual_remote_austin"]
        if comp_check(job, floor) is False:
            return f"comp: {kind} role, listed range midpoint is below ${floor:,.0f} ({shown})", []
    else:  # nyc, with no remote option
        floor = comp["min_annual_nyc"]
        flags.append("NYC")
        ok = comp_check(job, floor)
        if ok is False:
            return f"comp: NYC role, listed range midpoint is below ${floor:,.0f} ({shown})", []
        if ok is None:
            flags.append("check comp")
    return None, flags


def prefilter(jobs: list[Job], rules: dict, drops: list[dict] | None = None) -> list[Job]:
    """Keep jobs that pass and set `job.flags`. If `drops` is given, append one
    entry per dropped job for the audit log."""
    kept = []
    for j in jobs:
        reason, flags = evaluate(j, rules)
        if reason is None:
            j.flags = flags
            kept.append(j)
        elif drops is not None:
            drops.append({"company": j.company, "title": j.title, "location": j.location,
                          "url": j.url, "reason": reason})
    return kept


def write_drop_log(total: int, kept: list[Job], drops: list[dict], path: Path = LOG_PATH) -> None:
    """Write the "prefilter" section of logs/last_run.json, leaving any other
    section in the file alone. One role per line so a large file stays readable."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc = doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        doc = {}
    doc.pop("prefilter", None)
    counts = Counter(d["reason"] if d["reason"].startswith(("title", "seniority"))
                     else d["reason"].split(" (")[0] for d in drops)

    def rows(items):
        return "[\n    " + ",\n    ".join(json.dumps(x, ensure_ascii=False) for x in items) + "\n  ]" if items else "[]"

    section = [
        f'"time": {json.dumps(datetime.now().isoformat(timespec="seconds"))}',
        f'"input": {total}', f'"kept": {len(kept)}', f'"dropped": {len(drops)}',
        f'"drop_counts": {json.dumps(dict(counts.most_common()), ensure_ascii=False)}',
        f'"kept_flags": {json.dumps(dict(Counter(f for j in kept for f in j.flags)))}',
        '"kept_roles": ' + rows([{"company": j.company, "title": j.title, "location": j.location,
                                  "flags": j.flags, "url": j.url} for j in kept]),
        '"drops": ' + rows(drops),
    ]
    parts = [f"{json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}" for k, v in doc.items()]
    parts.append('"prefilter": {\n  ' + ",\n  ".join(section) + "\n}")
    path.parent.mkdir(exist_ok=True)
    path.write_text("{\n" + ",\n".join(parts) + "\n}\n", encoding="utf-8")
