import tempfile
import unittest
from pathlib import Path
from unittest import mock

from role_catcher import emailer
from role_catcher.models import Job, ScoredJob

RESULT = {"fit_score": 82, "reasoning": "Built enablement systems at Meta, a strong match.",
          "comp_if_listed": "$150k–$180k"}
def scored(**kw):
    job = Job(ats="greenhouse", company="Acme", job_id="1", title="Senior Manager, GTM Enablement",
              location="Remote (US)", url="https://boards.greenhouse.io/acme/jobs/1",
              posted_at="2026-10-09T14:00:00Z", flags=kw.get("flags", []))
    return ScoredJob(job=job, score=82, result=RESULT)


class RoleEmailTests(unittest.TestCase):
    def test_subject_format(self):
        self.assertEqual(emailer.role_subject(scored()),
                         "[Role Catcher] 82% | Senior Manager, GTM Enablement | Acme")

    def test_apply_link_first_and_no_banned_copy(self):
        subject, text, html = emailer.build_role_email(scored())
        self.assertTrue(text.startswith("APPLY: https://boards.greenhouse.io/acme/jobs/1"))
        self.assertLess(html.index("Apply here"), html.index("Senior Manager"))
        for out in (subject, text, html):
            self.assertNotIn("—", out)
            self.assertNotIn("–", out)
            self.assertNotIn("intersection", out.lower())

    def test_sections_present(self):
        _, text, _ = emailer.build_role_email(scored(flags=["NYC"]))
        for needle in ("Posted: Oct 9, 2026", "Comp: $150k-$180k", "Role fit: 82%",
                       "Check: NYC"):
            self.assertIn(needle, text)
        for gone in ("WHY I FIT", "GAPS OR RISKS", "Built enablement systems", "a strong match"):
            self.assertNotIn(gone, text)


class SendingTests(unittest.TestCase):
    CONFIG = {"timezone": "America/Chicago", "limits": {"max_emails_per_day": 2}}

    def setUp(self):
        p = mock.patch.object(emailer, "EMAIL_LOG", Path(tempfile.mkdtemp()) / "emails.jsonl")
        p.start()
        self.addCleanup(p.stop)

    def roles(self, n):
        out = []
        for i in range(n):
            s = scored()
            s.job.job_id = str(i)
            s.score = 90 - i
            out.append(s)
        return out

    def test_one_failure_does_not_stop_the_rest(self):
        calls = []

        def sender(subject, text, config, html):
            calls.append(subject)
            if len(calls) == 1:
                raise OSError("smtp down")

        cfg = {**self.CONFIG, "limits": {"max_emails_per_day": 10}}
        run = emailer.email_roles(self.roles(3), cfg, sender=sender)
        self.assertEqual((len(run.sent), len(run.failed)), (2, 1))
        self.assertEqual(run.failed[0].score, 90)   # failed one is left for retry, not logged as sent
        self.assertEqual(emailer.emails_sent_today("America/Chicago"), 2)

    def test_daily_cap_defers_lowest_scores(self):
        run = emailer.email_roles(self.roles(4), self.CONFIG, sender=lambda *a: None)
        self.assertEqual([s.score for s in run.sent], [90, 89])
        self.assertEqual(len(run.capped), 2)
        self.assertTrue(run.cap_hit)
        again = emailer.email_roles(self.roles(1), self.CONFIG, sender=lambda *a: None)
        self.assertEqual(len(again.sent), 0)       # cap counts earlier emails today

    def test_dry_run_sends_nothing_and_logs_nothing(self):
        sender = mock.Mock()
        run = emailer.email_roles(self.roles(1), self.CONFIG, dry_run=True, sender=sender)
        sender.assert_not_called()
        self.assertEqual(emailer.emails_sent_today("America/Chicago"), 0)
        self.assertEqual(len(run.sent), 1)
