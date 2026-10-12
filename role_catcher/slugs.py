"""Board slug discovery (section 3b, approach 2).

There is no official list of boards, so slugs come from two places:
  1. the watchlist: guess slugs from each company name and confirm against the
     official endpoints (or use slugs given explicitly in watchlist.yaml);
  2. Common Crawl's free URL index: harvest slugs from board URLs seen on the web.
Every slug is confirmed by the board's own API before it is trusted. Results are
kept in state/board_registry.json:

  {"boards": {"greenhouse:figma": {"company": "Figma", "source": "watchlist",
              "status": "live", "jobs": 151, "checked": "2026-10-12"}, ...},
   "unresolved": ["Company Name", ...], "last_full_sweep": "2026-10-12T..."}

Run by hand:  python -m role_catcher.slugs --watchlist | --harvest [--limit N]
"""
from __future__ import annotations
import argparse
import json
import logging
import re
import time
from datetime import date

import requests

from . import ats, state
from .config import load_yaml

log = logging.getLogger("role_catcher.slugs")
CC_INDEX = "https://index.commoncrawl.org/{crawl}-index"
CC_HOSTS = {
    "greenhouse": ["boards.greenhouse.io", "job-boards.greenhouse.io", "boards.eu.greenhouse.io",
                   "job-boards.eu.greenhouse.io"],
    "ashby": ["jobs.ashbyhq.com"],
    "lever": ["jobs.lever.co"],
}
_NOT_SLUGS = {"robots.txt", "embed", "favicon.ico", "sitemap.xml", "static", "assets", "v1", "login",
              "jobs", "api", "careers", "_next"}
_SUFFIXES = ("inc", "llc", "labs", "ai", "hq", "technologies", "co")


# ---- registry -------------------------------------------------------------

def load_registry() -> dict:
    reg = state.load("board_registry.json", {})
    reg.setdefault("boards", {})
    reg.setdefault("unresolved", [])
    return reg


def board_list(reg: dict, sources: tuple[str, ...] | None = None) -> list[dict]:
    out = []
    for key, b in sorted(reg["boards"].items()):
        if b.get("status") != "live" or (sources and b.get("source") not in sources):
            continue
        a, slug = key.split(":", 1)
        out.append({"ats": a, "slug": slug, "company": b.get("company") or slug})
    return out


# ---- watchlist resolution -------------------------------------------------

def slug_candidates(name: str) -> list[str]:
    """Plausible board slugs for a company name, most likely first."""
    base = name.lower().replace("&", " and ").replace(".", "")
    words = [w for w in re.split(r"[^a-z0-9]+", base) if w]
    if not words:
        return []
    cands = ["".join(words), "-".join(words)]
    trimmed = [w for w in words if w not in _SUFFIXES] or words
    cands += ["".join(trimmed), "-".join(trimmed)]
    cands += ["".join(words) + "hq", "".join(trimmed) + "hq"]
    if words[0] == "the" and len(words) > 1:
        cands.append("".join(words[1:]))
    seen, out = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _entry(item) -> dict:
    return {"name": item} if isinstance(item, str) else dict(item)


def resolve_company(item, *, session=None, sleep=time.sleep) -> tuple[list[dict], str]:
    """Find every live board for one watchlist entry. Returns (boards, note).
    Explicit slugs in the entry win; otherwise guessed slugs are tried on all
    three ATSes. Guesses on Greenhouse must also report a matching company name,
    which guards against a different company owning the same slug."""
    e = _entry(item)
    name = e["name"]
    if e.get("no_board"):
        return [], "no_board"
    found = []
    explicit = {a: e[a] for a in ats.URLS if e.get(a)}
    if explicit:
        for a, slug in explicit.items():
            try:
                jobs = ats.fetch_board(a, slug, name, session=session, sleep=sleep)
                found.append({"ats": a, "slug": slug, "company": name, "jobs": len(jobs)})
            except Exception as ex:
                log.warning("watchlist %s: explicit %s:%s failed: %s", name, a, slug, ex)
        return found, "explicit"
    for a in ats.URLS:
        for slug in slug_candidates(name):
            try:
                jobs = ats.fetch_board(a, slug, name, session=session, sleep=sleep)
            except ats.BoardNotFound:
                continue
            except Exception as ex:
                log.warning("watchlist %s: %s:%s error: %s", name, a, slug, ex)
                continue
            if a == "greenhouse" and jobs and not _names_match(name, jobs[0].company):
                log.warning("watchlist %s: greenhouse:%s belongs to %r, skipping", name, slug, jobs[0].company)
                continue
            if a != "greenhouse" and jobs and not _mentions(name, jobs):
                log.warning("watchlist %s: %s:%s never mentions the company, skipping", name, a, slug)
                continue
            found.append({"ats": a, "slug": slug, "company": name, "jobs": len(jobs)})
            break  # first live slug per ATS
    return found, "guessed"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _mentions(name: str, jobs: list, sample: int = 10) -> bool:
    """Ashby and Lever don't return a company name, so confirm a guessed slug by
    finding the company's name in the text of a few of its postings."""
    needle = re.sub(r"\s+", " ", name.lower().replace("&", "and")).strip()
    return any(needle in re.sub(r"\s+", " ", (j.description + " " + j.title).lower()) for j in jobs[:sample])


def _names_match(a: str, b: str) -> bool:
    x, y = _norm(a), _norm(b)
    return bool(x and y and (x in y or y in x))


def resolve_watchlist(reg: dict, watchlist: list, *, session=None, sleep=time.sleep,
                      workers: int = 6) -> dict:
    from concurrent.futures import ThreadPoolExecutor
    today = date.today().isoformat()
    reg["boards"] = {k: b for k, b in reg["boards"].items() if b.get("source") != "watchlist"}  # re-resolve fresh
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda w: resolve_company(w, session=session, sleep=sleep), watchlist))
    unresolved = []
    for item, (boards, note) in zip(watchlist, results):
        name = _entry(item)["name"]
        if note == "no_board":
            continue
        if not boards:
            unresolved.append(name)
        for b in boards:
            reg["boards"][f"{b['ats']}:{b['slug']}"] = {
                "company": name, "source": "watchlist", "status": "live",
                "jobs": b["jobs"], "checked": today}
    reg["unresolved"] = sorted(unresolved)
    return reg


# ---- Common Crawl harvest -------------------------------------------------

def latest_crawls(n: int = 3, *, session=None) -> list[str]:
    http = session or requests
    r = http.get("https://index.commoncrawl.org/collinfo.json", timeout=30,
                 headers={"User-Agent": ats.USER_AGENT})
    return [c["id"] for c in r.json()][:n]


def slug_from_url(url: str) -> str | None:
    m = re.match(r"https?://[^/]+/([^/?#]+)", url)
    if not m:
        return None
    slug = m.group(1).lower()
    if slug in _NOT_SLUGS or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", slug):
        return None
    return slug


def harvest_host(host: str, crawls: list[str], *, session=None, sleep=time.sleep,
                 limit: int = 100000) -> set[str]:
    """Slugs seen under one host across the given crawls. The index server is
    flaky (504s), so each query retries and a failure only skips that crawl."""
    http = session or requests
    slugs: set[str] = set()
    for crawl in crawls:
        for attempt in range(4):
            try:
                r = http.get(CC_INDEX.format(crawl=crawl), timeout=180,
                             params={"url": f"{host}/*", "output": "json", "fl": "url", "limit": limit},
                             headers={"User-Agent": ats.USER_AGENT})
                if r.status_code == 200:
                    for line in r.text.splitlines():
                        try:
                            s = slug_from_url(json.loads(line)["url"])
                        except (ValueError, KeyError):
                            continue
                        if s:
                            slugs.add(s)
                    break
                log.warning("commoncrawl %s %s: HTTP %s", crawl, host, r.status_code)
            except requests.RequestException as e:
                log.warning("commoncrawl %s %s: %s", crawl, host, e)
            sleep(2 ** attempt * 2)
    return slugs


def harvest(reg: dict, *, limit: int | None = None, session=None, sleep=time.sleep,
            workers: int = 8) -> tuple[dict, dict]:
    """Harvest slugs from Common Crawl, confirm each against the board API, add
    live ones to the registry. Returns (registry, stats). Known slugs are not
    re-checked here (the daily sweep refreshes them)."""
    from concurrent.futures import ThreadPoolExecutor
    crawls = latest_crawls(3, session=session)
    stats = {"crawls": crawls}
    candidates = []
    for a, hosts in CC_HOSTS.items():
        slugs = set()
        for h in hosts:
            slugs |= harvest_host(h, crawls, session=session, sleep=sleep)
        new = sorted(s for s in slugs if f"{a}:{s}" not in reg["boards"])
        stats[a] = {"harvested": len(slugs), "new": len(new)}
        candidates += [{"ats": a, "slug": s, "company": s} for s in new]
    if limit:
        candidates = candidates[:limit]
    today = date.today().isoformat()

    def check(b):
        try:
            jobs = ats.fetch_board(b["ats"], b["slug"], b["slug"], session=session, sleep=sleep)
            return b, len(jobs), (jobs[0].company if jobs and b["ats"] == "greenhouse" else None)
        except ats.BoardNotFound:
            return b, None, None
        except Exception as e:
            log.warning("harvest check %s:%s: %s", b["ats"], b["slug"], e)
            return b, None, None
        finally:
            sleep(0.2)

    live = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for b, n, company in pool.map(check, candidates):
            key = f"{b['ats']}:{b['slug']}"
            if n is None:
                reg["boards"][key] = {"company": b["slug"], "source": "commoncrawl", "status": "dead",
                                      "jobs": 0, "checked": today}
                continue
            live += 1
            reg["boards"][key] = {"company": company or b["slug"], "source": "commoncrawl",
                                  "status": "live", "jobs": n, "checked": today}
    stats["checked"] = len(candidates)
    stats["live"] = live
    return reg, stats


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--watchlist", action="store_true", help="resolve watchlist.yaml companies to board slugs")
    ap.add_argument("--harvest", action="store_true", help="harvest slugs from Common Crawl")
    ap.add_argument("--limit", type=int, help="cap harvested slugs to check (testing)")
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()
    reg = load_registry()
    if args.watchlist:
        reg = resolve_watchlist(reg, load_yaml("watchlist.yaml").get("companies") or [])
        live = [k for k, b in reg["boards"].items() if b["source"] == "watchlist" and b["status"] == "live"]
        print(f"watchlist: {len(live)} live boards; unresolved ({len(reg['unresolved'])}): {reg['unresolved']}")
    if args.harvest:
        reg, stats = harvest(reg, limit=args.limit)
        print("commoncrawl:", json.dumps(stats))
    if not args.no_save and (args.watchlist or args.harvest):
        state.save("board_registry.json", reg)


if __name__ == "__main__":
    main()
