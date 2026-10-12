"""Run: python -m unittest discover tests   (fixtures only; no network)"""
import unittest
from unittest import mock

import requests

from role_catcher import ats


class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._b, self.headers = status, body, headers or {}

    def json(self):
        return self._b


class ParseTests(unittest.TestCase):
    def test_greenhouse(self):
        data = {"jobs": [{"id": 1, "title": " Marketing Engineer ", "company_name": "Figma",
                          "location": {"name": "Remote, US"}, "absolute_url": "https://x/1",
                          "first_published": "2026-06-05T16:57:53-04:00",
                          "content": "&lt;p&gt;Do things.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Pay $127,000 - $296,000 a year&lt;/li&gt;&lt;/ul&gt;"}]}
        j = ats.parse_greenhouse(data, "fallback")[0]
        self.assertEqual((j.ats, j.company, j.job_id, j.title), ("greenhouse", "Figma", "1", "Marketing Engineer"))
        self.assertIn("Do things.", j.description)
        self.assertNotIn("<", j.description)
        self.assertEqual(j.comp, "$127,000 - $296,000")
        self.assertEqual(j.posted_at, "2026-06-05T20:57:53+00:00")

    def test_ashby_skips_unlisted_and_adds_remote(self):
        data = {"jobs": [
            {"id": "a", "title": "GTM Engineer", "location": "New York, NY", "isRemote": True,
             "jobUrl": "https://y/a", "descriptionPlain": "text", "publishedAt": "2026-04-07T17:12:35.753+00:00",
             "compensation": {"scrapeableCompensationSalarySummary": "$211K - $290K"}},
            {"id": "b", "title": "Hidden", "isListed": False}]}
        jobs = ats.parse_ashby(data, "Ramp")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].location, "New York, NY | Remote")
        self.assertEqual(jobs[0].comp, "$211K - $290K")

    def test_lever(self):
        data = [{"id": "z", "text": "Solutions Architect", "hostedUrl": "https://l/z", "createdAt": 1786469891368,
                 "workplaceType": "remote", "categories": {"allLocations": ["Austin, TX"]},
                 "descriptionBodyPlain": "Body", "additionalPlain": "EEO",
                 "lists": [{"text": "Requirements", "content": "<li>SQL</li>"}],
                 "salaryRange": {"currency": "USD", "interval": "per-year-salary", "min": 150000, "max": 200000}}]
        j = ats.parse_lever(data, "Acme")[0]
        self.assertEqual(j.location, "Austin, TX | Remote")
        self.assertIn("Requirements", j.description)
        self.assertIn("SQL", j.description)
        self.assertTrue(j.posted_at.startswith("2026-"))
        self.assertIn("150,000", j.comp)

    def test_comp_floor(self):
        self.assertEqual(ats.parse_comp_range("$211.4K - $290.6K"), (211400, 290600))
        self.assertEqual(ats.parse_comp_range("USD 150,000 - 200,000 per year"), (150000, 200000))
        self.assertTrue(ats.comp_meets_floor("$130,000 - $150,000", 130000))
        self.assertTrue(ats.comp_meets_floor("$110K - $140K", 130000))
        self.assertFalse(ats.comp_meets_floor("$90,000 - $120,000", 130000))
        self.assertIsNone(ats.comp_meets_floor(None, 130000))
        self.assertIsNone(ats.comp_meets_floor("Competitive", 130000))

    def test_pay_range_absent(self):
        self.assertIsNone(ats.find_pay_range("competitive salary"))


class FetchTests(unittest.TestCase):
    def test_retries_then_succeeds(self):
        sess = mock.Mock()
        sess.get.side_effect = [Resp(503), Resp(429, headers={"Retry-After": "3"}), Resp(200, {"jobs": []})]
        sleeps = []
        self.assertEqual(ats.get_json("u", session=sess, sleep=sleeps.append), {"jobs": []})
        self.assertEqual(sleeps, [1, 3])

    def test_404_is_not_found(self):
        sess = mock.Mock()
        sess.get.return_value = Resp(404)
        with self.assertRaises(ats.BoardNotFound):
            ats.get_json("u", session=sess, sleep=lambda s: None)

    def test_gives_up_after_retries(self):
        sess = mock.Mock()
        sess.get.side_effect = requests.ConnectionError("down")
        with self.assertRaises(ats.BoardError):
            ats.get_json("u", session=sess, sleep=lambda s: None)

    def test_lever_falls_back_to_eu(self):
        sess = mock.Mock()
        sess.get.side_effect = [Resp(404), Resp(200, [])]
        self.assertEqual(ats.fetch_board("lever", "x", session=sess, sleep=lambda s: None), [])
        self.assertIn("api.eu.lever.co", sess.get.call_args_list[1][0][0])

    def test_lever_error_body(self):
        sess = mock.Mock()
        sess.get.return_value = Resp(200, {"ok": False, "error": "Document not found"})
        with self.assertRaises(ats.BoardNotFound):
            ats.fetch_board("lever", "x", session=sess, sleep=lambda s: None)

    def test_fetch_boards_continues_past_failures(self):
        def fake(ats_name, slug, company=None, **kw):
            if slug == "bad":
                raise ats.BoardNotFound(slug)
            return [ats.Job(ats_name, "C", "1", "t", "l", "u")]
        with mock.patch.object(ats, "fetch_board", side_effect=fake):
            jobs, errors = ats.fetch_boards(
                [{"ats": "ashby", "slug": "bad"}, {"ats": "ashby", "slug": "ok"}], sleep=lambda s: None)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(errors[0]["kind"], "not_found")


if __name__ == "__main__":
    unittest.main()
