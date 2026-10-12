"""Run: python -m unittest discover tests   (no network)"""
import unittest
from datetime import datetime, timezone

from role_catcher.catchup import digest_blocks, order_for_cap, posted_within
from role_catcher.models import Job, ScoredJob

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def job(company, i, posted):
    return Job("greenhouse", company, str(i), "t", "l", "u", posted_at=posted)


class CatchupTests(unittest.TestCase):
    def test_window_excludes_old_and_undated(self):
        jobs = [job("A", 1, "2026-10-10T00:00:00+00:00"), job("A", 2, "2026-09-30T00:00:00+00:00"),
                job("A", 3, None), job("A", 4, "2026-10-05T12:00:00Z")]
        kept = posted_within(jobs, 7, NOW)
        self.assertEqual([j.job_id for j in kept], ["1", "4"])

    def test_order_watchlist_then_newest(self):
        jobs = [job("Zed", 1, "2026-10-11T00:00:00+00:00"), job("Figma", 2, "2026-10-08T00:00:00+00:00"),
                job("Figma", 3, "2026-10-10T00:00:00+00:00")]
        self.assertEqual([j.job_id for j in order_for_cap(jobs, {"figma"})], ["3", "2", "1"])

    def test_digest_lists_best_first(self):
        s = [ScoredJob(job("A", 1, None), 70), ScoredJob(job("B", 2, None), 90)]
        bullets = [b for k, b in digest_blocks(s, 7, "x") if k == "bullets"]
        self.assertTrue(bullets[0][0][0].startswith("90%"))


if __name__ == "__main__":
    unittest.main()
