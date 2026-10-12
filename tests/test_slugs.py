"""Run: python -m unittest discover tests   (no network)"""
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from role_catcher import ats, slugs
from role_catcher.discovery import sweep_due
from role_catcher.models import Job


class SlugTests(unittest.TestCase):
    def test_candidates(self):
        c = slugs.slug_candidates("Chili Piper")
        self.assertEqual(c[:2], ["chilipiper", "chili-piper"])
        self.assertIn("ramphq", slugs.slug_candidates("Ramp"))
        self.assertIn("openai", slugs.slug_candidates("OpenAI"))
        self.assertIn("humansand", slugs.slug_candidates("humans&"))
        self.assertIn("botcompany", slugs.slug_candidates("The Bot Company"))
        self.assertNotIn("", slugs.slug_candidates("AG1"))

    def test_slug_from_url(self):
        f = slugs.slug_from_url
        self.assertEqual(f("https://boards.greenhouse.io/Figma/jobs/123?x=1"), "figma")
        self.assertEqual(f("https://jobs.ashbyhq.com/0g"), "0g")
        self.assertIsNone(f("https://jobs.lever.co/robots.txt"))
        self.assertIsNone(f("https://job-boards.greenhouse.io/"))
        self.assertIsNone(f("https://boards.greenhouse.io/embed/job_board?for=x"))

    def test_name_checks(self):
        self.assertTrue(slugs._names_match("Chili Piper", "Chili Piper, Inc."))
        self.assertFalse(slugs._names_match("Genesis AI", "Genesis Health"))
        j = Job("ashby", "x", "1", "t", "l", "u", description="About Genesis AI: we build")
        self.assertTrue(slugs._mentions("Genesis AI", [j]))
        self.assertFalse(slugs._mentions("Protocol Labs", [j]))

    def test_explicit_slugs_win_and_no_board(self):
        with mock.patch.object(ats, "fetch_board", return_value=[1, 2]) as fb:
            boards, note = slugs.resolve_company({"name": "Glean", "greenhouse": "gleanwork"})
        self.assertEqual((note, boards[0]["slug"], boards[0]["jobs"]), ("explicit", "gleanwork", 2))
        self.assertEqual(fb.call_count, 1)
        self.assertEqual(slugs.resolve_company({"name": "X", "no_board": True}), ([], "no_board"))

    def test_resolve_skips_other_companys_greenhouse_slug(self):
        def fb(a, slug, name, **kw):
            if a == "greenhouse" and slug == "genesisai":
                return [Job("greenhouse", "Genesis Health", "1", "t", "l", "u")]
            raise ats.BoardNotFound(slug)
        with mock.patch.object(ats, "fetch_board", side_effect=fb):
            boards, _ = slugs.resolve_company("Genesis AI")
        self.assertEqual(boards, [])

    def test_board_list_filters_dead_and_source(self):
        reg = {"boards": {"greenhouse:a": {"status": "live", "source": "watchlist", "company": "A"},
                          "ashby:b": {"status": "dead", "source": "commoncrawl"},
                          "lever:c": {"status": "live", "source": "commoncrawl"}}}
        self.assertEqual([b["slug"] for b in slugs.board_list(reg)], ["a", "c"])
        self.assertEqual([b["slug"] for b in slugs.board_list(reg, ("watchlist",))], ["a"])


class SweepTests(unittest.TestCase):
    def test_sweep_due(self):
        now = datetime(2026, 10, 12, tzinfo=timezone.utc)
        self.assertTrue(sweep_due({}, 24, now))
        recent = {"last_full_sweep": (now - timedelta(hours=3)).isoformat()}
        old = {"last_full_sweep": (now - timedelta(hours=30)).isoformat()}
        self.assertFalse(sweep_due(recent, 24, now))
        self.assertTrue(sweep_due(old, 24, now))


if __name__ == "__main__":
    unittest.main()
