"""Lesson class summaries: evidence scoping, request limits and saved rows. Fictional data only."""
import io
import json
import os
import unittest
from unittest.mock import patch

from school_workflow_hub.adapters import class_summary_writer as writer
from school_workflow_hub.workflows import assessment_records as ar


def records():
    answer = {"question_id": "Q1", "stage": "DRAFT", "critical_aspect": "Producers as the entry point",
              "conceptual_level": "Proficient", "slide_number": "7", "response": "x" * 900,
              "evidence_note": "TEACHER ONLY"}
    base = {"class_key": "biology", "section_id": "c", "section_name": "Period C", "assignment_key": "B1",
            "included_in_counts": True, "assessment_stage": "final", "status": "proposed",
            "notes_required": "2", "notes_completed": "2", "answers": [answer]}
    return {"assignments": [{"assignment_key": "B1", "class_key": "biology", "assignment_title": "Fictional lesson"}],
            "submissions": [dict(base, record_id="a", count_key="k1", display_name="Ava K."),
                            dict(base, record_id="a2", count_key="k1", display_name="Ava K."),
                            dict(base, record_id="b", count_key="k2", display_name="Ben L.", included_in_counts=False),
                            dict(base, record_id="c", count_key="k3", display_name="Cal M.", section_id="d"),
                            dict(base, record_id="d", count_key="k4", display_name="Dee N.", assignment_key="B2")]}


class EvidenceTests(unittest.TestCase):
    def test_evidence_is_one_lesson_one_section_matched_students_without_teacher_notes(self):
        evidence = writer.build_evidence(records(), class_key="biology", section_id="c", assignment_key="B1")
        self.assertEqual(evidence["lesson"], "Fictional lesson")
        self.assertEqual([s["student"] for s in evidence["students"]], ["Ava K."])
        answer = evidence["students"][0]["answers"][0]
        self.assertEqual(answer["answer_type"], "revised answer")
        self.assertEqual(len(answer["words"]), writer.WORDS_LIMIT)
        self.assertNotIn("TEACHER ONLY", json.dumps(evidence))

    def test_request_shape_is_closed_before_any_read_or_call(self):
        with patch.object(writer, "authorize"), patch.object(writer, "_identity_configuration", return_value=("a", set())), \
                patch.object(writer.assessment_provider, "configured", return_value=True), \
                patch.object(writer.assessment_provider, "_call") as call:
            for bad in ({"class_key": "biology"}, {"class_key": "biology", "section_id": "c", "assignment_key": "B 1"},
                        {"class_key": "biology", "section_id": "c", "assignment_key": "B1", "extra": "x"}):
                with self.assertRaises(ValueError):
                    writer.draft("assertion", bad, {})
            call.assert_not_called()


class LessonSummaryRowTests(unittest.TestCase):
    def summary(self, **extra):
        return dict({"class_key": "biology", "section_id": "c", "section_name": "Period C",
                     "summary": "An idea worth chasing\nText.", "coverage": "3 students"}, **extra)

    def test_lesson_summary_names_its_assignment_and_allows_the_longer_report(self):
        row = ar.summary_row(self.summary(assignment_key="B1", summary="a" * 12000), now="2026-10-03T00:00:00Z")
        self.assertEqual(row["assignment_key"], "B1")
        with self.assertRaises(ValueError):
            ar.summary_row(self.summary(assignment_key="B1", summary="a" * 12001), now="n")

    def test_section_only_summary_keeps_the_old_limit_and_shape(self):
        self.assertNotIn("assignment_key", ar.summary_row(self.summary(), now="n"))
        with self.assertRaises(ValueError):
            ar.summary_row(self.summary(summary="a" * 1601), now="n")
        with self.assertRaises(ValueError):
            ar.summary_row(self.summary(assignment_key="bad key"), now="n")


class RouteTests(unittest.TestCase):
    def test_draft_route_requires_post_from_the_dashboard_origin(self):
        from school_workflow_hub import web
        statuses = []
        def start(status, headers):
            statuses.append(status)
        body = b'{"class_key":"biology","section_id":"c","assignment_key":"B1"}'
        env = {"REQUEST_METHOD": "POST", "PATH_INFO": "/api/class-summary-draft", "CONTENT_TYPE": "application/json",
               "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body), "HTTP_ORIGIN": "https://elsewhere.example"}
        with patch.dict(os.environ, {"HUB_STATE_PROBE_ORIGINS": "https://hub.example"}):
            web.application(env, start)
            web.application({"REQUEST_METHOD": "GET", "PATH_INFO": "/api/class-summary-draft"}, start)
        self.assertEqual(statuses, ["403 Forbidden", "405 Method Not Allowed"])


if __name__ == "__main__":
    unittest.main()
