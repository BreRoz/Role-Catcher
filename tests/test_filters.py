"""Run: python -m unittest discover tests   (uses the real filters.yaml; no network)"""
import json
import tempfile
import unittest
from pathlib import Path

from role_catcher import filters
from role_catcher.config import load_yaml
from role_catcher.models import Job

RULES = load_yaml("filters.yaml")


def job(title, location="Remote, US", comp=None, desc=""):
    return Job(ats="greenhouse", company="Acme", job_id="1", title=title, location=location,
               url="https://x/1", description=desc, comp=comp)


def verdict(title, **kw):
    return filters.evaluate(job(title, **kw), RULES)


class TitleTests(unittest.TestCase):
    def test_includes(self):
        for t in ["Director, Sales Enablement", "Senior Manager, GTM Systems", "GTM Engineer",
                  "Head of Go-to-Market Operations", "Principal Solutions Architect",
                  "Director of Sales Engineering", "Revenue Operations Lead", "RevOps Manager",
                  "Senior Manager, L&D", "AI & Marketing Automation Manager", "Forward Deployed Engineer",
                  "GenAI Solutions Designer", "Staff Product Marketing Manager", "Senior Manager, Knowledge Management"]:
            self.assertIsNone(verdict(t)[0], t)

    def test_no_include_match_is_dropped(self):
        reason, _ = verdict("Senior Manager, Payroll")
        self.assertEqual(reason, "title: no include pattern matched")

    def test_case_and_punctuation_insensitive(self):
        self.assertIsNone(verdict("SALES-ENABLEMENT director")[0])

    def test_whole_words_only(self):
        # "internal" must not trip the "intern" exclude; "enablements" is not "enablement"
        self.assertIsNone(verdict("Director, Internal Enablement")[0])
        self.assertIsNotNone(verdict("Enablements Lead")[0])

    def test_excludes(self):
        for t, term in [("Sales Enablement Intern", "intern"), ("Junior GTM Engineer", "junior"),
                        ("SDR Enablement Manager", "sdr"), ("Software Engineer, GTM Systems", "software engineer"),
                        ("Sales Enablement Coordinator", "coordinator"), ("Account Executive, Enterprise Solutions Engineer", "account executive"),
                        ("Sales Enablement Associate", "associate"), ("Product Marketing Designer", "designer"),
                        ("Backend GTM Engineer", "backend"), ("Data Scientist, GTM", "data scientist")]:
            self.assertEqual(verdict(t)[0], f"title: excluded by '{term}'", t)

    def test_exclude_exceptions(self):
        self.assertIsNone(verdict("Associate Director, Sales Enablement")[0])
        self.assertIsNone(verdict("Solutions Designer, GenAI")[0])

    def test_specialist_only_when_jd_is_systems_building(self):
        self.assertEqual(verdict("Sales Enablement Specialist", desc="Run onboarding sessions.")[0],
                         "title: excluded by 'specialist'")
        heavy = "Build workflow automation and integration systems via our API; own the infrastructure."
        self.assertIsNone(verdict("GTM Systems Specialist", desc=heavy)[0])

    def test_program_manager_needs_pairing(self):
        self.assertIsNone(verdict("Senior Program Manager, Sales Enablement")[0])
        self.assertIsNone(verdict("Program Manager, GTM Strategy")[0])
        self.assertIsNone(verdict("Program Manager, Internal Systems")[0])
        self.assertEqual(verdict("Program Manager, Facilities")[0], "title: no include pattern matched")

    def test_seniority_ambiguous_passes_clearly_junior_drops(self):
        self.assertIsNone(verdict("Sales Enablement Manager")[0])
        self.assertIsNone(verdict("Enablement Lead")[0])
        self.assertTrue(verdict("Revenue Operations Analyst")[0].startswith("seniority"))


class LocationTests(unittest.TestCase):
    def kind(self, loc):
        return filters.location_class(loc, RULES["location"])

    def test_remote_us(self):
        for loc in ["Remote", "Remote, US", "Remote - United States", "United States (Remote)",
                    "San Francisco, CA | Remote", "Remote - Canada or US", "Remote, Americas", "Remote (U.S.)"]:
            self.assertEqual(self.kind(loc), "remote", loc)

    def test_remote_outside_us_is_other(self):
        for loc in ["Remote - UK", "Remote, EMEA", "Remote - Canada", "Remote (Latin America)", "Remote - Germany"]:
            self.assertEqual(self.kind(loc), "other", loc)

    def test_austin_and_nyc(self):
        self.assertEqual(self.kind("Austin, TX"), "austin")
        self.assertEqual(self.kind("New York, NY"), "nyc")
        self.assertEqual(self.kind("NYC (Hybrid)"), "nyc")
        self.assertEqual(self.kind("Austin, TX; New York, NY"), "austin")

    def test_other_and_ambiguous(self):
        self.assertEqual(self.kind("San Francisco, CA"), "other")
        self.assertEqual(self.kind("London, UK"), "other")
        self.assertEqual(self.kind(""), "ambiguous")
        self.assertEqual(self.kind("Multiple Locations"), "ambiguous")

    def test_bare_remote_next_to_only_foreign_places_is_not_us_remote(self):
        # Ashby tacks "Remote" onto the location list of any remote-eligible posting
        for loc in ["Tokyo, Japan | Remote", "Sydney | Remote", "Riyadh | Doha | Dubai | Remote",
                    "Canada | Toronto | Remote", "Japan_Tokyo | Remote"]:
            self.assertEqual(self.kind(loc), "other", loc)
        for loc in ["London | Remote | New York", "Washington, DC | Remote", "San Francisco | Remote"]:
            self.assertEqual(self.kind(loc), "remote", loc)

    def test_remote_naming_us_and_canada_is_us_remote(self):
        self.assertEqual(self.kind("Remote (United States | Canada)"), "remote")
        self.assertEqual(self.kind("Hybrid, London"), "other")

    def test_recruiting_titles_with_gtm_are_dropped(self):
        for t in ["Talent Acquisition Partner, GTM", "GTM Recruiting", "People Partner, GTM"]:
            self.assertTrue(verdict(t)[0].startswith("title: excluded"), t)

    def test_remote_with_foreign_region_in_title_is_dropped(self):
        self.assertTrue(verdict("Solutions Architect, EMEA", location="Remote")[0].startswith("location:"))
        self.assertIsNone(verdict("Solutions Architect, US and EMEA", location="Remote")[0])

    def test_nationwide_united_states_next_to_cities_is_kept_for_checking(self):
        # a late-stage role (Figma Marketing Engineer) lists exactly this
        reason, flags = verdict("Marketing Engineer", location="San Francisco, CA • New York, NY • United States")
        self.assertEqual((reason, flags), (None, ["check location"]))

    def test_ambiguous_is_kept_and_flagged(self):
        reason, flags = verdict("GTM Engineer", location="Multiple Locations")
        self.assertIsNone(reason)
        self.assertEqual(flags, ["check location"])

    def test_unwanted_location_dropped_with_reason(self):
        reason, _ = verdict("GTM Engineer", location="San Francisco, CA")
        self.assertTrue(reason.startswith("location:") and "San Francisco" in reason)


class CompTests(unittest.TestCase):
    def test_remote_floor_uses_bottom_of_range(self):
        self.assertIsNone(verdict("GTM Engineer", comp="$130,000 - $180,000")[0])
        self.assertTrue(verdict("GTM Engineer", comp="$120,000 - $200,000")[0].startswith("comp:"))

    def test_no_or_unparseable_comp_is_never_a_reason_to_drop(self):
        self.assertEqual(verdict("GTM Engineer"), (None, []))
        self.assertEqual(verdict("GTM Engineer", comp="Competitive")[0], None)

    def test_nyc_flagged_and_needs_high_comp(self):
        self.assertEqual(verdict("GTM Engineer", location="New York, NY", comp="$200K - $250K"), (None, ["NYC"]))
        self.assertTrue(verdict("GTM Engineer", location="New York, NY", comp="$150K - $250K")[0].startswith("comp: NYC"))

    def test_nyc_without_comp_is_check_comp(self):
        self.assertEqual(verdict("GTM Engineer", location="New York, NY"), (None, ["NYC", "check comp"]))

    def test_nyc_plus_remote_uses_remote_floor_and_no_nyc_flag(self):
        self.assertEqual(verdict("GTM Engineer", location="New York, NY | Remote", comp="$140K - $160K"), (None, []))

    def test_any_pay_band_in_description_can_satisfy_floor(self):
        desc = "Pay: $90,000 - $110,000 in Denver; $140,000 - $170,000 in New York."
        self.assertIsNone(verdict("GTM Engineer", desc=desc)[0])


class PrefilterAndLogTests(unittest.TestCase):
    def setUp(self):
        self.jobs = [job("Director, Sales Enablement"), job("SDR Manager"),
                     job("GTM Engineer", location="New York, NY"), job("RevOps Lead", location="Paris, France")]

    def test_keeps_sets_flags_and_collects_drops(self):
        drops = []
        kept = filters.prefilter(self.jobs, RULES, drops)
        self.assertEqual([j.title for j in kept], ["Director, Sales Enablement", "GTM Engineer"])
        self.assertEqual(kept[1].flags, ["NYC", "check comp"])
        self.assertEqual(len(drops), 2)
        self.assertTrue(all(d["reason"] and d["url"] for d in drops))

    def test_log_is_valid_json_and_keeps_other_sections(self):
        drops = []
        kept = filters.prefilter(self.jobs, RULES, drops)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "last_run.json"
            path.write_text(json.dumps({"scoring": {"scored": 3}}))
            filters.write_drop_log(len(self.jobs), kept, drops, path)
            doc = json.loads(path.read_text())
            self.assertEqual(doc["scoring"], {"scored": 3})
            self.assertEqual((doc["prefilter"]["input"], doc["prefilter"]["kept"], doc["prefilter"]["dropped"]), (4, 2, 2))
            self.assertEqual(len(doc["prefilter"]["drops"]), 2)
            filters.write_drop_log(0, [], [], path)  # rewriting replaces only the prefilter section
            self.assertEqual(json.loads(path.read_text())["scoring"], {"scored": 3})


if __name__ == "__main__":
    unittest.main()
