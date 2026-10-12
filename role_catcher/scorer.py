"""Step 3: LLM role-fit scoring against profile.md (section 5)."""
from __future__ import annotations
import hashlib
import logging
from dataclasses import dataclass

from . import llm
from .config import ROOT
from .models import Job, ScoredJob

log = logging.getLogger("role_catcher.scorer")

FLAVORS = [
    "knowledge systems", "sales/revenue enablement", "GTM ops/tooling", "content ops",
    "AI integration/AI-forward enablement", "GTM engineering", "other",
]

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["fit_score", "role_flavor", "seniority_match", "location_ok", "top_reasons",
                 "gaps", "deal_breakers_hit", "comp_if_listed", "one_line_summary"],
    "properties": {
        "fit_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "role_flavor": {"type": "string", "enum": FLAVORS},
        "seniority_match": {"type": "boolean"},
        "location_ok": {"type": "boolean"},
        "top_reasons": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 3},
        "gaps": {"type": "array", "items": {"type": "string"}},
        "deal_breakers_hit": {"type": "array", "items": {"type": "string"}},
        "comp_if_listed": {"type": ["string", "null"]},
        "one_line_summary": {"type": "string", "minLength": 1},
    },
}

MAX_DESCRIPTION_CHARS = 24000   # guards against pathological postings; real JDs are far shorter


def description_hash(job: Job) -> str:
    return hashlib.sha256((job.description or "").strip().encode("utf-8")).hexdigest()[:16]


def passes_gate(result: dict, threshold: int) -> bool:
    """Section 5 pass condition: score at/above threshold, seniority and location
    OK, and no deal-breakers."""
    return (
        result.get("fit_score", 0) >= threshold
        and bool(result.get("seniority_match"))
        and bool(result.get("location_ok"))
        and not result.get("deal_breakers_hit")
    )


def load_system_prompt(include_calibration: bool = True) -> str:
    """Stable text sent first on every call (so the provider can cache it):
    rubric, profile, then optional short calibration summaries. calibrate.py
    leaves the summaries out so it never grades a role against its own summary."""
    parts = [
        (ROOT / "rubric.md").read_text(encoding="utf-8").strip(),
        "# Candidate profile (source of truth)\n\n" + (ROOT / "profile.md").read_text(encoding="utf-8").strip(),
    ]
    summaries = ROOT / "calibration" / "summaries.md"
    if include_calibration and summaries.exists() and summaries.read_text(encoding="utf-8").strip():
        parts.append("# Calibration: roles that reached late or final interview rounds and should "
                     "score roughly 75+\n\n" + summaries.read_text(encoding="utf-8").strip())
    return "\n\n".join(parts)


def format_job(job: Job) -> str:
    lines = [
        f"Company: {job.company}",
        f"Title: {job.title}",
        f"Location: {job.location or 'not listed'}",
        f"Listed compensation: {job.comp or 'not listed'}",
    ]
    if job.flags:
        lines.append(f"Pre-filter flags: {', '.join(job.flags)}")
    lines += ["", "Job description:", (job.description or "")[:MAX_DESCRIPTION_CHARS]]
    return "\n".join(lines)


def score_one(client, config: dict, job: Job, system_prompt: str,
              purpose: str = "scoring") -> tuple[dict, float]:
    """One scoring call. Raises llm.LLMError if the output is invalid twice."""
    res = llm.complete(client, config, "scoring", system_prompt, format_job(job),
                       schema=SCHEMA, schema_name="role_fit", purpose=purpose)
    return res.data, res.cost


def _priority(job: Job, watch: set[str]) -> tuple:
    # watchlist first, then newest first, then stable
    return (job.company.lower() not in watch, -_ts(job.posted_at), job.key)


def _ts(iso: str | None) -> float:
    from datetime import datetime
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


@dataclass
class ScoreRun:
    scored: list           # list[ScoredJob], every job that has a result (fresh or cached)
    unscored: list         # list[Job] deferred by the cap or failed; NOT to be marked seen
    calls: int = 0
    cache_hits: int = 0
    failures: int = 0
    cost: float = 0.0
    cap_hit: bool = False


def score_jobs(jobs: list[Job], config: dict, cache: dict | None = None,
               client=None, watchlist: set[str] | None = None) -> ScoreRun:
    """Score jobs, returning a ScoredJob for each (whatever its score; the caller
    applies the gate). `cache` is the state/score_cache.json dict, updated in place:
    {job.key: {"hash": description hash, "result": {...}}}. A cached result is
    reused only while the description hash is unchanged. Per-call cap comes from
    limits.max_roles_scored_per_run and counts only real model calls. One failure
    never stops the run."""
    cache = cache if cache is not None else {}
    cap = config["limits"]["max_roles_scored_per_run"]
    watch = {w.lower() for w in (watchlist or set())}
    run = ScoreRun(scored=[], unscored=[])
    system_prompt = None

    for job in sorted(jobs, key=lambda j: _priority(j, watch)):
        h = description_hash(job)
        hit = cache.get(job.key)
        if hit and hit.get("hash") == h:
            run.scored.append(ScoredJob(job=job, score=hit["result"]["fit_score"],
                                        reasons=hit["result"]["top_reasons"],
                                        result=hit["result"], from_cache=True))
            run.cache_hits += 1
            continue
        if run.calls >= cap:
            if not run.cap_hit:
                log.warning("scoring cap hit (%d calls this run); remaining roles deferred to the next run", cap)
                print(f"CAP HIT: scoring cap of {cap} calls reached; remaining roles deferred.")
            run.cap_hit = True
            run.unscored.append(job)
            continue
        if client is None:
            client = llm.get_client(config)
        if system_prompt is None:
            system_prompt = load_system_prompt()
        run.calls += 1
        try:
            result, cost = score_one(client, config, job, system_prompt)
        except Exception as e:      # one bad call must never stop the run
            run.failures += 1
            run.unscored.append(job)
            log.error("scoring failed for %s (%s): %s", job.title, job.company, e)
            print(f"scoring failed: {job.company} | {job.title}: {e}")
            continue
        run.cost += cost
        cache[job.key] = {"hash": h, "result": result}
        run.scored.append(ScoredJob(job=job, score=result["fit_score"],
                                    reasons=result["top_reasons"], result=result))

    print(f"scoring: {len(run.scored)} scored ({run.calls - run.failures} new calls, {run.cache_hits} cached), "
          f"{run.failures} failed, {len(run.unscored)} deferred/unscored, ${run.cost:.4f}")
    return run
