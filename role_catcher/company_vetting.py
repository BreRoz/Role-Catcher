"""Step 4: company health vetting with 60-day cache (section 6).

The model researches and reports facts (with web search). The pass / flag / block
verdict is then computed in code from the thresholds in config.yaml, so the rules
are the ones you set, not whatever the model felt like. Sources come only from the
web search citations OpenRouter returns, never from the model's own list."""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlparse

from . import llm
from .models import ScoredJob
from .state import verdict_is_fresh

log = logging.getLogger("role_catcher.vetting")

# TrueUp's terms prohibit automated or AI-agent access and its robots.txt blocks
# AI crawlers, so its pages are never used or cited.
EXCLUDED_DOMAINS = ("trueup.io",)

TRAJECTORY = ["growing", "stable", "shrinking", "unknown"]
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["company", "verdict", "trajectory", "signals", "headcount_direction",
                 "layoff_pct_last_12mo", "employee_rating", "rating_review_count",
                 "winding_down_or_distress", "sources", "confidence"],
    "properties": {
        "company": {"type": "string"},
        "verdict": {"type": "string", "enum": ["pass", "flag", "block"]},
        "trajectory": {"type": "string", "enum": TRAJECTORY},
        "signals": {
            "type": "object", "additionalProperties": False,
            "required": ["headcount_trend", "hiring_trend", "funding_or_revenue_stage",
                         "recent_layoffs", "employee_sentiment", "reputation_notes"],
            "properties": {k: {"type": "string"} for k in
                           ["headcount_trend", "hiring_trend", "funding_or_revenue_stage",
                            "recent_layoffs", "employee_sentiment", "reputation_notes"]},
        },
        # Machine-readable versions of the signals above, used by the verdict rules.
        "headcount_direction": {"type": "string", "enum": TRAJECTORY},
        "layoff_pct_last_12mo": {"type": ["number", "null"]},
        "employee_rating": {"type": ["number", "null"]},
        "rating_review_count": {"type": ["integer", "null"]},
        "winding_down_or_distress": {"type": "boolean"},
        "sources": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
}

SYSTEM = """You research one company for a job seeker who does not want to work at a company on a low trajectory or with poor employee sentiment. Today is {today}.

Use web search. Look for, in priority order:
1. Company trajectory: headcount trend, hiring trend, funding or revenue stage, recent rounds, runway concerns, leadership exodus, signs of winding down or serious financial trouble.
2. Layoffs in the last 12 months (layoffs.fyi and news). Report the date and size, and the percent of staff if findable.
3. Employee sentiment from Glassdoor, Blind, Comparably or similar, from search results only. Never log in to or scrape those sites. If you cannot find a rating, say unknown. Never invent a rating or review count.

Rules:
* Every claim must come from the search results you were given. If you did not find it, write "unknown" or "none found". Never present a guess as a fact.
* Do not use or cite trueup.io.
* If there is no evidence either way, set trajectory "unknown" and confidence "low".
* Numeric fields: layoff_pct_last_12mo is the percent of staff laid off in the last 12 months (null if no layoff found or the size is not findable); employee_rating is out of 5 (null if not found); rating_review_count is the number of reviews behind it (null if not stated). winding_down_or_distress is true only if a credible report says the company is winding down or in serious financial trouble.
* headcount_direction is growing, stable, shrinking, or unknown, based on evidence.
* verdict: pass if growing or stable with no red flags; flag for one moderate negative signal, low confidence, or missing data; block for clearly shrinking headcount, a layoff of about 15 percent or more of staff in 12 months, a findable rating below about 3.3 with a meaningful number of reviews, or credible winding down or serious financial trouble.
* sources: list the URLs you actually relied on.
* Keep each text field to one or two plain sentences. Never use em dashes.

Return JSON only."""


@dataclass
class VetRun:
    approved: list = field(default_factory=list)   # ScoredJob, verdict set, not blocked
    blocked: list = field(default_factory=list)    # ScoredJob, verdict block (with reason)
    deferred: list = field(default_factory=list)   # ScoredJob not vetted (cap): retry next run
    calls: int = 0
    cache_hits: int = 0
    failures: int = 0
    cost: float = 0.0
    cap_hit: bool = False


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def clean_sources(citations: list[dict]) -> list[str]:
    out = []
    for c in citations:
        u = c.get("url") or ""
        if u and not any(_host(u) == d or _host(u).endswith("." + d) for d in EXCLUDED_DOMAINS) and u not in out:
            out.append(u)
    return out


def decide(facts: dict, sources: list[str], rules: dict) -> tuple[str, list[str]]:
    """Turn researched facts into (verdict, reasons) using config thresholds."""
    block, flag = [], []
    conf = facts.get("confidence", "low")
    pct = facts.get("layoff_pct_last_12mo")
    rating, n = facts.get("employee_rating"), facts.get("rating_review_count") or 0
    shrinking = facts.get("headcount_direction") == "shrinking" or facts.get("trajectory") == "shrinking"

    if facts.get("winding_down_or_distress"):
        block.append("credible report of winding down or serious financial trouble")
    if pct is not None and pct >= rules["block_layoff_pct"]:
        block.append(f"layoff of about {pct:g}% of staff in the last 12 months")
    if rating is not None and rating < rules["block_rating_below"] and n >= rules["min_reviews_for_rating"]:
        block.append(f"employee rating {rating:g}/5 across {n} reviews")
    if shrinking and rules.get("block_if_shrinking", True):
        (block if conf in ("high", "medium") else flag).append("headcount shrinking")
    elif shrinking:
        flag.append("headcount shrinking")
    if block:
        return "block", block

    if pct is not None and pct > 0:
        flag.append(f"layoff of about {pct:g}% of staff in the last 12 months")
    elif (facts.get("signals") or {}).get("recent_layoffs", "none found").lower() not in ("none found", "none", "unknown", ""):
        flag.append("layoffs reported in the last 12 months")
    if rating is not None and rating < rules["flag_rating_below"]:
        flag.append(f"employee rating {rating:g}/5" + (f" across {n} reviews" if n else ""))
    if rating is None and rules.get("flag_on_unknown_sentiment"):
        flag.append("employee sentiment unknown")
    if facts.get("trajectory") == "unknown":
        flag.append("trajectory unknown")
    if conf == "low":
        flag.append("low confidence")
    if not sources:
        flag.append("no sources found")
    return ("flag", flag) if flag else ("pass", [])


def research(client, config: dict, company: str, hint_url: str = "", purpose: str = "vetting") -> tuple[dict, float]:
    """One web-search research call. Returns (verdict record, cost). Raises llm.LLMError."""
    system = SYSTEM.format(today=date.today().isoformat())
    user = f"Company: {company}"
    if hint_url:
        user += f"\nThey are hiring (use this only to identify the right company): {hint_url}"
    res = llm.complete(client, config, "vetting", system, user, schema=SCHEMA,
                       schema_name="company_vetting", web_search=True, purpose=purpose)
    facts = res.data
    sources = clean_sources(res.citations)
    verdict, reasons = decide(facts, sources, config["vetting"]["rules"])
    record = {
        "company": company,
        "verdict": verdict,
        "reasons": reasons,
        "model_verdict": facts["verdict"],
        "trajectory": facts["trajectory"],
        "signals": facts["signals"],
        "headcount_direction": facts["headcount_direction"],
        "layoff_pct_last_12mo": facts["layoff_pct_last_12mo"],
        "employee_rating": facts["employee_rating"],
        "rating_review_count": facts["rating_review_count"],
        "winding_down_or_distress": facts["winding_down_or_distress"],
        "sources": sources,
        "confidence": facts["confidence"] if sources else "low",
        "date": date.today().isoformat(),
    }
    return record, res.cost


def watchlist_record(company: str) -> dict:
    return {"company": company, "verdict": "watchlist", "reasons": [], "sources": [],
            "trajectory": "unknown", "confidence": "n/a",
            "note": "On your watchlist, not vetted."}


def vet_companies(scored: list[ScoredJob], config: dict, cache: dict | None = None,
                  watchlist: set[str] | None = None, client=None) -> VetRun:
    """Attach a company verdict to each role that passed fit scoring. Watchlist
    companies are skipped. Verdicts are cached for vetting.cache_days. A research
    failure never stops the run: that role goes out flagged (low confidence) and the
    failure is not cached, so the company is retried on its next role."""
    cache = cache if cache is not None else {}
    watch = {w.lower() for w in (watchlist or set())}
    days = config["vetting"]["cache_days"]
    cap = config["limits"]["max_companies_vetted_per_run"]
    run = VetRun()
    this_run: dict[str, dict] = {}

    for s in scored:
        name = s.job.company
        key = name.lower()
        if key in watch:
            s.company_verdict = watchlist_record(name)
            run.approved.append(s)
            continue
        verdict = this_run.get(key)
        if verdict is None:
            cached = cache.get(key)
            if cached and verdict_is_fresh(cached, days):
                verdict = cached
                run.cache_hits += 1
            elif run.calls >= cap:
                if not run.cap_hit:
                    log.warning("vetting cap hit (%d calls this run)", cap)
                    print(f"CAP HIT: company vetting cap of {cap} calls reached; remaining roles deferred.")
                run.cap_hit = True
                run.deferred.append(s)
                continue
            else:
                if client is None:
                    client = llm.get_client(config)
                run.calls += 1
                try:
                    verdict, cost = research(client, config, name, s.job.url)
                    run.cost += cost
                    cache[key] = verdict
                except Exception as e:   # never stop the run
                    run.failures += 1
                    log.error("vetting failed for %s: %s", name, e)
                    print(f"vetting failed: {name}: {e}")
                    verdict = {"company": name, "verdict": "flag", "confidence": "low",
                               "trajectory": "unknown", "sources": [], "signals": {},
                               "reasons": ["company research failed; not checked"],
                               "date": date.today().isoformat()}
            this_run[key] = verdict
        s.company_verdict = verdict
        (run.blocked if verdict["verdict"] == "block" else run.approved).append(s)

    print(f"vetting: {len(run.approved)} approved, {len(run.blocked)} blocked, {len(run.deferred)} deferred "
          f"({run.calls} calls, {run.cache_hits} cached, {run.failures} failed, ${run.cost:.4f})")
    return run
