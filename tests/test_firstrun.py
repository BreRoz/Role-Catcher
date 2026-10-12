"""Run: python -m unittest discover tests   (no network)"""
import unittest

from role_catcher.firstrun import cap_for_backfill, is_first_run, seed_seen
from role_catcher.main import blocked, unseen
from role_catcher.models import Job


def job(company, i):
    return Job("greenhouse", company, str(i), "t", "l", "u")


class FirstRunTests(unittest.TestCase):
    def test_first_run_detection(self):
        self.assertTrue(is_first_run({}))
        self.assertFalse(is_first_run({"greenhouse:a:1": "2026-10-12"}))

    def test_seed_marks_everything_once(self):
        seen = {}
        jobs = [job("A", 1), job("A", 2)]
        self.assertEqual(seed_seen(seen, jobs, "2026-10-12"), 2)
        self.assertEqual(seed_seen(seen, jobs + [job("B", 3)], "2026-10-13"), 1)
        self.assertEqual(seen["greenhouse:A:1"], "2026-10-12")  # original date kept

    def test_backfill_cap_prefers_watchlist(self):
        jobs = [job("Zed", 1), job("Acme", 2), job("Figma", 3), job("Figma", 4)]
        kept, skipped = cap_for_backfill(jobs, {"figma"}, 3)
        self.assertEqual([j.company for j in kept], ["Figma", "Figma", "Acme"])
        self.assertEqual(skipped, 1)
        self.assertEqual(cap_for_backfill(jobs, set(), 0), ([], 4))

    def test_new_board_jobs_are_seeded_not_scored(self):
        seen = {"greenhouse:A:0": "d"}
        old, new = job("A", 1), job("B", 2)
        new.new_board = True
        baseline = [j for j in [old, new] if j.new_board]
        seed_seen(seen, baseline, "today")
        self.assertIn(new.key, seen)
        self.assertNotIn(old.key, seen)

    def test_dedup_skips_seen_and_ignores_reposts_of_same_id(self):
        seen = {job("A", 1).key: "2026-10-01"}
        jobs = [job("A", 1), job("A", 2), job("B", 1)]    # same id at another company is a different role
        self.assertEqual([j.key for j in unseen(jobs, seen)],
                         ["greenhouse:A:2", "greenhouse:B:1"])
        self.assertEqual(unseen(jobs, {}), jobs)

    def test_blocklist(self):
        jobs = [job("Bad Co", 1), job("Good Co", 2)]
        self.assertEqual([j.company for j in blocked(jobs, ["bad co"])], ["Good Co"])
        self.assertEqual(len(blocked(jobs, None)), 2)


if __name__ == "__main__":
    unittest.main()
