from datetime import datetime
import json
from pathlib import Path
import unittest

from school_workflow_hub.core.config import ConfigError, load_config, parse_config
from school_workflow_hub.core.schedule import preview_job_schedule


ROOT = Path(__file__).resolve().parents[1]


class WorkflowScheduleTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(ROOT / "config/example.json")

    def test_all_jobs_disabled_and_all_three_course_rules(self):
        self.assertEqual(set(self.config.course_rules), {"biology", "forensics", "anatomy"})
        self.assertTrue(all(job["enabled"] is False for job in self.config.schedules.values()))
        self.assertIn("class_note_crawl", self.config.schedules)

    def test_no_school_and_dependency_skip(self):
        now = datetime.fromisoformat("2026-09-18T07:15:00-04:00")
        result = preview_job_schedule(self.config, "morning_summary", as_of=now)
        self.assertEqual(result["reason"], "dependency_incomplete")
        result = preview_job_schedule(self.config, "morning_summary", as_of=now,
                                      no_school_dates=("2026-09-18",), completed_jobs=("submission_crawl",))
        self.assertEqual(result["reason"], "no_school_skip")

    def test_biweekly_and_rerun(self):
        kwargs = {"completed_jobs": ("submission_crawl",)}
        for day, expected in (("2026-09-18", "due_preview"), ("2026-09-25", "outside_interval"), ("2026-10-02", "due_preview")):
            result = preview_job_schedule(self.config, "biweekly_reports", as_of=datetime.fromisoformat(day + "T07:15:00-04:00"), **kwargs)
            self.assertEqual(result["reason"], expected)
            self.assertFalse(result["scheduled"])
        result = preview_job_schedule(self.config, "biweekly_reports", as_of=datetime.fromisoformat("2026-09-18T07:15:00-04:00"), completed_jobs=("submission_crawl", "biweekly_reports"))
        self.assertEqual(result["reason"], "already_completed")

    def test_enabled_job_configuration_fails(self):
        payload = json.loads((ROOT / "config/example.json").read_text())
        payload["schedules"]["morning_summary"]["enabled"] = True
        with self.assertRaises(ConfigError):
            parse_config(payload)

    def test_shared_bounds_match_workflow_contracts(self):
        for key, value in (("retention_days", 13), ("retention_days", 3651), ("research_assignment_threshold", 1001)):
            payload = json.loads((ROOT / "config/example.json").read_text())
            payload["workflow_policy"][key] = value
            with self.assertRaises(ConfigError):
                parse_config(payload)
