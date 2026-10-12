import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from role_catcher import company_vetting as cv, spend
from role_catcher.config import load_config
from role_catcher.models import Job, ScoredJob

FACTS = {"company": "Acme", "verdict": "pass", "trajectory": "growing",
         "signals": {"headcount_trend": "up", "hiring_trend": "up", "funding_or_revenue_stage": "Series B",
                     "recent_layoffs": "none found", "employee_sentiment": "4.2 on Glassdoor",
                     "reputation_notes": ""},
         "headcount_direction": "growing", "layoff_pct_last_12mo": None, "employee_rating": 4.2,
         "rating_review_count": 300, "winding_down_or_distress": False,
         "sources": ["x"], "confidence": "high"}
SRC = ["https://example.com/a"]


class Resp:
    def __init__(self, facts, urls=("https://example.com/a", "https://trueup.io/co/acme")):
        ann = [{"type": "url_citation", "url_citation": {"url": u, "title": ""}} for u in urls]
        self.d = {"model": "m", "choices": [{"message": {"content": json.dumps(facts), "annotations": ann}}],
                  "usage": {"cost": 0.02}}

    def model_dump(self):
        return self.d


class Client:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0
        self.chat = mock.Mock()
        self.chat.completions.create = self._create

    def _create(self, **kw):
        self.calls += 1
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def sj(company):
    j = Job(ats="greenhouse", company=company, job_id="1", title="Dir", location="Remote",
            url="http://x", description="d")
    return ScoredJob(job=j, score=80)


class Tests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()
        self.rules = self.cfg["vetting"]["rules"]
        tmp = Path(tempfile.mkdtemp()) / "spend.jsonl"
        for p in (mock.patch.object(spend, "LOG_PATH", tmp),
                  mock.patch.object(spend.log_call, "__defaults__", (tmp,))):
            p.start(); self.addCleanup(p.stop)

    def d(self, **kw):
        return cv.decide({**FACTS, **kw}, SRC, self.rules)

    def test_pass(self):
        self.assertEqual(self.d()[0], "pass")

    def test_blocks(self):
        self.assertEqual(self.d(layoff_pct_last_12mo=15)[0], "block")
        self.assertEqual(self.d(employee_rating=3.1, rating_review_count=200)[0], "block")
        self.assertEqual(self.d(headcount_direction="shrinking")[0], "block")
        self.assertEqual(self.d(winding_down_or_distress=True)[0], "block")

    def test_low_rating_with_few_reviews_flags_not_blocks(self):
        self.assertEqual(self.d(employee_rating=3.0, rating_review_count=5)[0], "flag")

    def test_flags(self):
        self.assertEqual(self.d(layoff_pct_last_12mo=6)[0], "flag")
        self.assertEqual(self.d(confidence="low")[0], "flag")
        self.assertEqual(self.d(trajectory="unknown")[0], "flag")
        self.assertEqual(cv.decide(FACTS, [], self.rules)[0], "flag")   # no sources
        self.assertEqual(self.d(employee_rating=None, rating_review_count=None)[0], "pass")

    def test_shrinking_low_confidence_only_flags(self):
        self.assertEqual(self.d(headcount_direction="shrinking", confidence="low")[0], "flag")

    def test_trueup_is_never_a_source(self):
        c = Client([Resp(FACTS)])
        rec, _ = cv.research(c, self.cfg, "Acme")
        self.assertEqual(rec["sources"], ["https://example.com/a"])

    def test_cache_hit_within_60_days_and_expiry(self):
        cache, c = {}, Client([Resp(FACTS), Resp(FACTS)])
        run = cv.vet_companies([sj("Acme"), sj("Acme")], self.cfg, cache, client=c)
        self.assertEqual((c.calls, len(run.approved)), (1, 2))      # one call serves both roles
        run = cv.vet_companies([sj("Acme")], self.cfg, cache, client=c)
        self.assertEqual((c.calls, run.cache_hits), (1, 1))         # fresh cache: no call
        cache["acme"]["date"] = (date.today() - timedelta(days=61)).isoformat()
        cv.vet_companies([sj("Acme")], self.cfg, cache, client=c)
        self.assertEqual(c.calls, 2)                                # stale: re-researched

    def test_watchlist_skipped(self):
        c = Client([])
        run = cv.vet_companies([sj("Vip")], self.cfg, {}, {"vip"}, client=c)
        self.assertEqual(c.calls, 0)
        self.assertEqual(run.approved[0].company_verdict["note"], "On your watchlist, not vetted.")

    def test_block_removes_role_and_failure_flags_without_caching(self):
        c = Client([Resp({**FACTS, "layoff_pct_last_12mo": 20}), RuntimeError("down")])
        cache = {}
        run = cv.vet_companies([sj("Bad"), sj("Down")], self.cfg, cache, client=c)
        self.assertEqual([s.job.company for s in run.blocked], ["Bad"])
        self.assertEqual(run.approved[0].company_verdict["verdict"], "flag")
        self.assertEqual(run.failures, 1)
        self.assertNotIn("down", cache)

    def test_cap_defers(self):
        cfg = {**self.cfg, "limits": {**self.cfg["limits"], "max_companies_vetted_per_run": 1}}
        c = Client([Resp(FACTS)])
        run = cv.vet_companies([sj("A"), sj("B")], cfg, {}, client=c)
        self.assertEqual((len(run.approved), len(run.deferred), run.cap_hit), (1, 1, True))


if __name__ == "__main__":
    unittest.main()
