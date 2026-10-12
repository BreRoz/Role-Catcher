import tempfile
import unittest
from pathlib import Path
from unittest import mock

from role_catcher import emailer
from role_catcher.models import Job, ScoredJob

RESULT = {"fit_score": 82, "one_line_summary": "Owns the enablement knowledge base — a strong match.",
          "top_reasons": ["Built enablement systems", "AI workflow experience", "Remote US"],
          "gaps": ["No direct CRM admin work", "Intersection of sales and product is new"],
          "comp_if_listed": "$150k–$180k"}
PASS = {"verdict": "pass", "trajectory": "growing", "confidence": "high", "reasons": [],
        "sources": ["https://example.com/a"],
        "signals": {"headcount_trend": "up 12%", "recent_layoffs": "none found",
                    "employee_sentiment": "4.2 on Glassdoor"}}


def scored(verdict=PASS, **kw):
    job = Job(ats="greenhouse", company="Acme", job_id="1", title="Senior Manager, GTM Enablement",
              location="Remote (US)", url="https://boards.greenhouse.io/acme/jobs/1",
              posted_at="2026-10-09T14:00:00Z", flags=kw.get("flags", []))
    return ScoredJob(job=job, score=82, reasons=RESULT["top_reasons"], result=RESULT, company_verdict=verdict)


class RoleEmailTests(unittest.TestCase):
    def test_subject_format(self):
        self.assertEqual(emailer.role_subject(scored()),
                         "[Role Catcher] 82% | Senior Manager, GTM Enablement | Acme | Growing")

    def test_watchlist_wording(self):
        from role_catcher.company_vetting import watchlist_record
        v = watchlist_record("Acme")
        _, text, _ = emailer.build_role_email(scored(v))
        self.assertIn("On your watchlist, not vetted.", text)
        self.assertIn("| Watchlist", emailer.role_subject(scored(v)))

    def test_flag_warning_comes_first_in_company_section(self):
        v = {**PASS, "verdict": "flag", "reasons": ["layoff of about 8% of staff in the last 12 months"]}
        _, text, _ = emailer.build_role_email(scored(v))
        first = text.split("COMPANY HEALTH")[1].strip().splitlines()[0]
        self.assertTrue(first.startswith("Warning: layoff of about 8%"))

    def test_research_failure_says_not_checked(self):
        v = {**PASS, "verdict": "flag", "reasons": ["company research failed; not checked"]}
        self.assertIn("| Not checked", emailer.role_subject(scored(v)))

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
        for needle in ("Posted: Oct 9, 2026", "Comp: $150k-$180k", "WHY I FIT", "GAPS OR RISKS",
                       "Confidence: High", "Check: NYC", "https://example.com/a"):
            self.assertIn(needle, text)


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
