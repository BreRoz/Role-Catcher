"""Official job board endpoints (section 3a): Greenhouse, Ashby, Lever.

Each fetcher takes a board slug and returns normalized `Job` objects. No
scraping: these are the public JSON APIs the boards themselves publish.
Discovery of *which* slugs exist is a separate step (3b).
"""
from __future__ import annotations
import html
import logging
import re
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from .models import Job

log = logging.getLogger("role_catcher.ats")

USER_AGENT = "RoleCatcher/1.0 (personal job-alert tool; contact bre999@gmail.com)"

URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
}
LEVER_EU = "https://api.eu.lever.co/v0/postings/{slug}?mode=json"


class BoardNotFound(Exception):
    """The slug does not exist on this ATS (404 or an explicit not-found body)."""


class BoardError(Exception):
    """The board could not be fetched after retries."""


def get_json(url: str, *, session: requests.Session | None = None, retries: int = 4,
             timeout: int = 30, sleep=time.sleep):
    """GET JSON with backoff on 429/5xx/network errors. Honors Retry-After.
    Raises BoardNotFound on 404, BoardError when retries run out."""
    http = session or requests
    last = None
    for attempt in range(retries):
        try:
            r = http.get(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                         timeout=timeout)
        except requests.RequestException as e:
            last = e
        else:
            if r.status_code == 404:
                raise BoardNotFound(url)
            if r.status_code == 429 or r.status_code >= 500:
                last = f"HTTP {r.status_code}"
                try:
                    wait = float(r.headers.get("Retry-After", ""))
                except ValueError:
                    wait = 0
                if attempt < retries - 1:
                    sleep(max(wait, 2 ** attempt))
                continue
            if r.status_code >= 400:
                raise BoardError(f"{url}: HTTP {r.status_code}")
            try:
                return r.json()
            except ValueError as e:
                raise BoardError(f"{url}: invalid JSON") from e
        if attempt < retries - 1:
            sleep(2 ** attempt)
    raise BoardError(f"{url}: {last}")


def html_to_text(raw: str | None) -> str:
    if not raw:
        return ""
    soup = BeautifulSoup(raw, "html.parser")
    for li in soup.find_all("li"):
        li.insert_before("\n- ")
    text = soup.get_text("\n")
    text = text.replace("\xa0", " ")
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


_PAY_RE = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?\s?[kK]?\s*(?:-|–|—|to)\s*\$?\s?\d[\d,]*(?:\.\d+)?\s?[kK]?")


def find_pay_range(text: str) -> str | None:
    """Fallback for boards that bury pay in the description (Greenhouse)."""
    m = _PAY_RE.search(text or "")
    return re.sub(r"\s+", " ", m.group(0)).strip() if m else None


_NUM_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s?([kK])?")


def parse_comp_range(comp: str | None) -> tuple[float, float] | None:
    """Annual (low, high) in dollars from strings like '$211.4K - $290.6K' or
    '$165,000 - $190,000'. None if unparseable or clearly not annual pay."""
    if not comp:
        return None
    nums = []
    for m in _NUM_RE.finditer(comp):
        v = float(m.group(1).replace(",", ""))
        if m.group(2):
            v *= 1000
        nums.append(v)
    nums = [n for n in nums if n >= 20000]  # drops years, hours, equity percents
    if not nums:
        return None
    return min(nums), max(nums)


def comp_meets_floor(comp: str | None, floor: float) -> bool | None:
    """True if the top of the listed range reaches `floor`, False if it does
    not, None if no usable comp is listed (callers flag 'check comp')."""
    r = parse_comp_range(comp)
    return None if r is None else r[1] >= floor


def _iso_from_ms(ms) -> str | None:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _iso_utc(s: str | None) -> str | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat()


# ---- Greenhouse -----------------------------------------------------------

def parse_greenhouse(data: dict, company: str) -> list[Job]:
    jobs = []
    for j in data.get("jobs") or []:
        # `content` arrives HTML-escaped, so unescape before stripping tags.
        desc = html_to_text(html.unescape(j.get("content") or ""))
        jobs.append(Job(
            ats="greenhouse",
            company=j.get("company_name") or company,
            job_id=str(j["id"]),
            title=(j.get("title") or "").strip(),
            location=((j.get("location") or {}).get("name") or "").strip(),
            url=j.get("absolute_url") or "",
            description=desc,
            posted_at=_iso_utc(j.get("first_published")),
            comp=find_pay_range(desc),
        ))
    return jobs


# ---- Ashby ----------------------------------------------------------------

def _ashby_comp(j: dict) -> str | None:
    c = j.get("compensation")
    if not isinstance(c, dict):
        return None
    return c.get("scrapeableCompensationSalarySummary") or c.get("compensationTierSummary") or None


def parse_ashby(data: dict, company: str) -> list[Job]:
    jobs = []
    for j in data.get("jobs") or []:
        if j.get("isListed") is False:
            continue
        loc = (j.get("location") or "").strip()
        extra = [s.get("location") for s in (j.get("secondaryLocations") or []) if isinstance(s, dict)]
        parts = [p for p in [loc, *extra] if p]
        if j.get("isRemote") and not any("remote" in p.lower() for p in parts):
            parts.append("Remote")
        desc = j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml"))
        jobs.append(Job(
            ats="ashby",
            company=company,
            job_id=str(j["id"]),
            title=(j.get("title") or "").strip(),
            location=" | ".join(parts),
            url=j.get("jobUrl") or j.get("applyUrl") or "",
            description=desc.strip(),
            posted_at=_iso_utc(j.get("publishedAt")),
            comp=_ashby_comp(j),
        ))
    return jobs


# ---- Lever ----------------------------------------------------------------

def _lever_comp(j: dict) -> str | None:
    r = j.get("salaryRange")
    if isinstance(r, dict) and r.get("min") is not None:
        cur = r.get("currency") or ""
        per = f" per {r['interval']}" if r.get("interval") else ""
        return f"{cur} {r['min']:,} - {r.get('max', r['min']):,}{per}".strip()
    return j.get("salaryDescriptionPlain") or None


def parse_lever(data: list, company: str) -> list[Job]:
    jobs = []
    for j in data or []:
        cats = j.get("categories") or {}
        locs = cats.get("allLocations") or ([cats["location"]] if cats.get("location") else [])
        parts = list(locs)
        if (j.get("workplaceType") or "").lower() == "remote" and not any("remote" in p.lower() for p in parts):
            parts.append("Remote")
        sections = [j.get("openingPlain") or "", j.get("descriptionBodyPlain") or j.get("descriptionPlain") or ""]
        for item in j.get("lists") or []:
            sections.append(f"{item.get('text', '')}\n{html_to_text(item.get('content'))}")
        sections.append(j.get("additionalPlain") or "")
        desc = "\n\n".join(s.strip() for s in sections if s and s.strip())
        jobs.append(Job(
            ats="lever",
            company=company,
            job_id=str(j["id"]),
            title=(j.get("text") or "").strip(),
            location=" | ".join(parts),
            url=j.get("hostedUrl") or "",
            description=desc,
            posted_at=_iso_from_ms(j.get("createdAt")),
            comp=_lever_comp(j),
        ))
    return jobs


# ---- Entry points ---------------------------------------------------------

def fetch_board(ats: str, slug: str, company: str | None = None, *,
                session: requests.Session | None = None, sleep=time.sleep) -> list[Job]:
    """Fetch and normalize one board. Raises BoardNotFound / BoardError;
    callers catch these so one bad board never stops the run."""
    name = company or slug
    if ats not in URLS:
        raise ValueError(f"unknown ats: {ats}")
    url = URLS[ats].format(slug=slug)
    try:
        data = get_json(url, session=session, sleep=sleep)
    except BoardNotFound:
        if ats != "lever":
            raise
        data = get_json(LEVER_EU.format(slug=slug), session=session, sleep=sleep)  # EU-hosted boards
    if ats == "lever":
        if isinstance(data, dict):  # {"ok": false, "error": ...}
            raise BoardNotFound(f"lever:{slug}: {data.get('error', 'unexpected response')}")
        return parse_lever(data, name)
    if ats == "greenhouse":
        return parse_greenhouse(data, name)
    return parse_ashby(data, name)


def fetch_boards(boards: list[dict], *, session: requests.Session | None = None,
                 sleep=time.sleep, pause: float = 0.3) -> tuple[list[Job], list[dict]]:
    """boards: [{ats, slug, company?}]. Returns (jobs, errors). Never raises."""
    jobs, errors = [], []
    for i, b in enumerate(boards):
        try:
            found = fetch_board(b["ats"], b["slug"], b.get("company"), session=session, sleep=sleep)
            jobs.extend(found)
            log.info("%s:%s -> %d jobs", b["ats"], b["slug"], len(found))
        except Exception as e:  # catch, log, continue
            log.warning("%s:%s failed: %s", b.get("ats"), b.get("slug"), e)
            errors.append({"ats": b.get("ats"), "slug": b.get("slug"), "error": str(e),
                           "kind": "not_found" if isinstance(e, BoardNotFound) else "error"})
        if pause and i < len(boards) - 1:
            sleep(pause)
    return jobs, errors
