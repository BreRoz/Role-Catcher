import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from role_catcher import health

NOW = datetime(2026, 10, 12, 18, 0, tzinfo=timezone.utc)   # a Monday, 1pm Central
CONFIG = {"timezone": "America/Chicago", "run_hours_local": [7, 10, 13, 16, 19]}


def run_rec(days_ago, gh=10, ashby=10, lever=10, **extra):
    ts = (NOW - timedelta(days=days_ago)).isoformat()
    by = {"greenhouse": {"tried": 5, "ok": 5, "errors": 0, "jobs": gh},
          "ashby": {"tried": 5, "ok": 5, "errors": 0, "jobs": ashby},
          "lever": {"tried": 5, "ok": 5, "errors": 0, "jobs": lever}}
    return {"ts": ts, "discovery": {"boards_tried": 15, "boards_ok": 15, "jobs_seen": gh + ashby + lever,
                                    "by_ats": by}, **extra}


def load(recs):
    return [{**r, "_when": datetime.fromisoformat(r["ts"])} for r in recs]


class SourceHealthTests(unittest.TestCase):
    def test_healthy_sources_no_problems(self):
        self.assertEqual(health.source_problems(load([run_rec(d) for d in range(20)]), NOW), [])

    def test_zero_for_two_weeks_is_an_alarm(self):
        runs = load([run_rec(d, lever=0) for d in range(16)])
        out = health.source_problems(runs, NOW)
        self.assertEqual(len(out), 1)
        self.assertIn("lever", out[0])
        self.assertNotIn("Early warning", out[0])

    def test_short_history_is_early_warning(self):
        out = health.source_problems(load([run_rec(d, ashby=0) for d in range(3)]), NOW)
        self.assertTrue(out[0].startswith("Early warning"))

    def test_one_good_run_clears_it(self):
        runs = [run_rec(d, lever=0) for d in range(1, 16)] + [run_rec(0)]
        self.assertEqual(health.source_problems(load(runs), NOW), [])


class WeeklyTests(unittest.TestCase):
    def test_due_only_monday_once(self):
        self.assertTrue(health.weekly_due(CONFIG, NOW, {}))
        self.assertFalse(health.weekly_due(CONFIG, NOW, {"last_weekly": "2026-10-12"}))
        self.assertFalse(health.weekly_due(CONFIG, NOW + timedelta(days=1), {}))

    def test_build_reports_blocks_spend_and_caps(self):
        d = Path(tempfile.mkdtemp())
        recs = [run_rec(i, scoring={"calls": 4, "cache_hits": 1, "failures": 1, "passed": 2},
                        vetting={"calls": 2, "cache_hits": 0, "failures": 0}, emails={"sent": 1},
                        new_roles=20, prefilter={"dropped": 15}, caps_hit=["scoring (150 calls per run)"])
                for i in range(1, 4)]
        (d / "runs.jsonl").write_text("\n".join(json.dumps(r) for r in recs))
        (d / "blocked.jsonl").write_text(json.dumps({
            "ts": (NOW - timedelta(days=1)).isoformat(), "company": "Badco", "title": "Head of Enablement",
            "url": "https://x.test/1", "score": 88, "reasons": ["layoff of about 20% of staff"]}) + "\n")
        (d / "spend.jsonl").write_text("\n".join(json.dumps(
            {"ts": (NOW - timedelta(days=1)).isoformat(), "purpose": p, "cost": c})
            for p, c in (("scoring", 0.5), ("vetting", 0.25))))
        subject, text, html = health.build_weekly(
            CONFIG, NOW, d / "runs.jsonl", d / "blocked.jsonl", d / "spend.jsonl",
            registry={"boards": {"greenhouse:a": {"company": "A", "status": "live"}}})
        self.assertIn("ATTENTION", subject)
        for needle in ("Badco: Head of Enablement (88%)", "layoff of about 20%", "Scoring: $0.50",
                       "Vetting: $0.25", "Discovery: $0.00", "Total OpenRouter spend: $0.75",
                       "Daily cap hit this week: scoring", "Role scoring calls failed: 3",
                       "Roles emailed: 3"):
            self.assertIn(needle, text)
        self.assertNotIn("—", text)

    def test_missing_logs_still_loud(self):
        d = Path(tempfile.mkdtemp())
        subject, text, _ = health.build_weekly(CONFIG, NOW, d / "r", d / "b", d / "s", registry={"boards": {}})
        self.assertIn("ATTENTION", subject)
        self.assertIn("No runs were recorded", text)
