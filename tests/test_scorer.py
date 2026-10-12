"""Run: python -m unittest discover tests   (fake client; no network, no key)"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from role_catcher import llm, scorer, spend
from role_catcher.config import load_config
from role_catcher.models import Job
from role_catcher.schema import validate

GOOD = {"reasoning": "Built enablement systems at Meta. Gap: no CRM admin.", "fit_score": 82,
        "seniority_match": True, "location_ok": True, "deal_breakers_hit": [], "comp_if_listed": None}


class Resp:
    def __init__(self, data, cost=0.01):
        self.d = {"model": "m", "choices": [{"message": {"content": json.dumps(data)}}],
                  "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": cost}}

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


def job(i, desc="Build enablement systems.", company="Acme"):
    return Job(ats="greenhouse", company=company, job_id=str(i), title="Director, GTM Enablement",
               location="Remote, US", url="http://x", description=desc)


class Tests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()
        tmp = Path(tempfile.mkdtemp()) / "spend.jsonl"
        for p in (mock.patch.object(spend, "LOG_PATH", tmp),
                  mock.patch.object(spend.log_call, "__defaults__", (tmp,))):
            p.start(); self.addCleanup(p.stop)

    def test_schema_accepts_good_and_rejects_bad(self):
        self.assertEqual(validate(GOOD, scorer.SCHEMA), [])
        self.assertTrue(validate({**GOOD, "reasoning": ""}, scorer.SCHEMA))
        self.assertTrue(validate({**GOOD, "fit_score": 101}, scorer.SCHEMA))

    def test_gate(self):
        g = scorer.passes_gate
        self.assertTrue(g(GOOD, 75))
        self.assertFalse(g({**GOOD, "fit_score": 74}, 75))
        self.assertFalse(g({**GOOD, "seniority_match": False}, 75))
        self.assertFalse(g({**GOOD, "location_ok": False}, 75))
        self.assertFalse(g({**GOOD, "deal_breakers_hit": ["cold outbound"]}, 75))
        self.assertTrue(g({**GOOD, "fit_score": 78}, 80) is False)

    def test_prompt_has_profile_and_rubric(self):
        p = scorer.load_system_prompt()
        self.assertIn("GTM Systems Builder", p)
        self.assertIn("not a stretch", p)
        self.assertIn("caps the score at 70", p)

    def test_cache_by_description_hash(self):
        cache, c = {}, Client([Resp(GOOD), Resp({**GOOD, "fit_score": 60})])
        r1 = scorer.score_jobs([job(1)], self.cfg, cache, client=c)
        self.assertEqual((r1.calls, r1.scored[0].score), (1, 82))
        r2 = scorer.score_jobs([job(1)], self.cfg, cache, client=c)       # same text: cached
        self.assertEqual((r2.calls, r2.cache_hits, c.calls), (0, 1, 1))
        r3 = scorer.score_jobs([job(1, "Edited text.")], self.cfg, cache, client=c)  # edited: re-scored
        self.assertEqual((r3.calls, r3.scored[0].score), (1, 60))

    def test_failure_does_not_stop_run_and_job_is_unscored(self):
        c = Client([RuntimeError("boom"), Resp(GOOD)])
        r = scorer.score_jobs([job(1), job(2)], self.cfg, {}, client=c)
        self.assertEqual((len(r.scored), len(r.unscored), r.failures), (1, 1, 1))

    def test_cap_defers_extra_roles_and_cache_hits_are_free(self):
        cfg = {**self.cfg, "limits": {**self.cfg["limits"], "max_roles_scored_per_run": 1}}
        cache = {job(3).key: {"hash": scorer.description_hash(job(3)), "result": GOOD}}
        c = Client([Resp(GOOD)])
        r = scorer.score_jobs([job(1), job(2), job(3)], cfg, cache, client=c)
        self.assertEqual((r.calls, r.cache_hits, len(r.unscored), r.cap_hit), (1, 1, 1, True))
        self.assertEqual(len(r.scored), 2)

    def test_daily_cap_defers_rest_but_cached_roles_still_pass(self):
        cache = {job(3).key: {"hash": scorer.description_hash(job(3)), "result": GOOD}}
        c = Client([Resp(GOOD), llm.CapReached("daily scoring cap of 200 calls")])
        r = scorer.score_jobs([job(1), job(2), job(3)], self.cfg, cache, client=c)
        self.assertEqual((r.calls, r.cache_hits, len(r.scored), len(r.unscored), r.failures), (1, 1, 2, 1, 0))
        self.assertTrue(r.cap_hit)
        self.assertIn("daily scoring cap", r.cap_reason)

    def test_watchlist_scored_first_under_cap(self):
        cfg = {**self.cfg, "limits": {**self.cfg["limits"], "max_roles_scored_per_run": 1}}
        c = Client([Resp(GOOD)])
        r = scorer.score_jobs([job(1, company="Other"), job(2, company="Vip")], cfg, {},
                              client=c, watchlist={"vip"})
        self.assertEqual(r.scored[0].job.company, "Vip")


if __name__ == "__main__":
    unittest.main()


class CalibrateFiles(unittest.TestCase):
    def test_read_job_parses_header_and_skips_placeholder(self):
        import calibrate
        with tempfile.TemporaryDirectory() as d:
            ok = Path(d) / "a.txt"
            ok.write_text("Company: Acme\nTitle: Enablement Lead\nLocation: Remote\n\nBody text.\n")
            job = calibrate.read_job(ok)
            self.assertEqual((job.company, job.title, job.location, job.description),
                             ("Acme", "Enablement Lead", "Remote", "Body text."))
            ph = Path(d) / "b.txt"
            ph.write_text(calibrate.PLACEHOLDER + "\nrest")
            self.assertIsNone(calibrate.read_job(ph))
