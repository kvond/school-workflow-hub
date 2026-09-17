"""Fictional private-workbook projection, complete bounded reads and auth tests."""
import copy
import json
import re
import unittest
from unittest.mock import patch

from school_workflow_hub.adapters.grading_records import HEADERS, _tables, project, read_records
from school_workflow_hub.adapters.google_sheets import SheetsReadError
from school_workflow_hub.adapters.hosted_agenda import ConnectionError
from school_workflow_hub.adapters.read_only_http import SourceReadError
from school_workflow_hub.web import application

SOURCE = "fictional_private_grading_12345"
CONFIG = {"HUB_IAP_AUDIENCE": "/projects/123/locations/us-central1/services/synthetic",
          "HUB_ALLOWED_EMAILS": "teacher@example.invalid", "HUB_GRADING_SPREADSHEET_ID": SOURCE}


def tables():
    return {"Assignments": [{"assignment_key": "f01", "class_key": "forensics", "assignment_title": "Fictional lesson"}],
        "Submissions": [{"record_id": "one", "assignment_key": "f01", "section_id": "section-a",
            "section_name": "Period A", "student_id": "fictional-1", "display_name": "Alex F.",
            "source_url": "https://example.invalid/work", "source_revision": "revision-one", "status": "proposed",
            "content_score": "6.25", "revision_score": "", "hold_reason": "", "feedback": "Explain the evidence.",
            "checked_at": "2026-09-01T12:00:00Z", "ic_posting_status": "not_posted"}],
        "Answers": [{"record_id": "one", "question_id": "q1", "stage": "NOTES", "response": "Fictional writing",
                     "conceptual_level": "Developing", "explanation_level": "Names idea", "meaningful_complete": "true"}],
        "Class summaries": [{"class_key": "forensics", "section_id": "section-a", "section_name": "Period A",
            "summary": "Ask students to connect a claim to their evidence.", "coverage": "Partial supplied work.",
            "checked_at": "2026-09-01T12:00:00Z"}]}


class Reader:
    def __init__(self):
        self.values, self.properties, self.calls = {}, [], []
        for number, (title, rows) in enumerate(tables().items()):
            headers = list(HEADERS[title]) + (["meaningful_complete"] if title == "Answers" else [])
            self.values[title] = [headers] + [[r.get(h, "") for h in headers] for r in rows]
            self.properties.append({"sheetId": number, "title": title,
                                    "gridProperties": {"rowCount": 700, "columnCount": 26}})

    def read(self, source, ranges=()):
        self.calls.append(ranges)
        if ranges:
            raise AssertionError("Grading cells must use the dedicated bounded bulk reader")
        return {"sheets": [{"properties": copy.deepcopy(p)} for p in self.properties]}

    def read_grading_tables(self, source, ranges):
        self.calls.append(ranges)
        sheets = []
        for cell_range in ranges:
            title, last = re.fullmatch(r"'([^']+)'!A1:[A-Z]+(\d+)", cell_range).groups()
            values = self.values[title][:int(last)]
            sheets.append({"properties": copy.deepcopy(next(p for p in self.properties if p["title"] == title)),
                "data": [{"startRow": 0, "startColumn": 0,
                          "rowData": [{"values": [{"formattedValue": v} for v in row]} for row in values]}]})
        return {"sheets": sheets}


class GradingRecordsTests(unittest.TestCase):
    def test_three_reads_preserve_the_complete_table_projection(self):
        reader = Reader()
        self.assertEqual(_tables(reader, SOURCE), tables())
        self.assertEqual(len(reader.calls), 3)

    def test_reads_whole_grids_and_keeps_answers_after_blank_gap(self):
        reader = Reader()
        reader.values["Answers"] += [[]] * 598
        reader.values["Answers"].append(["one", "q2", "DRAFT", "Later writing", "", "", "true"])
        result = project(_tables(reader, SOURCE))
        self.assertEqual(len(result["submissions"][0]["answers"]), 2)
        self.assertEqual(result["submissions"][0]["content_score"], 6.25)
        self.assertEqual(result["summaries"][0]["submitted_students"], 1)
        self.assertEqual(reader.calls, [(), tuple("'" + title + "'!A1:Z700" for title in HEADERS), ()])

    def test_separate_late_block_at_last_allowed_row_is_not_truncated(self):
        class SparseReader(Reader):
            def __init__(self):
                super().__init__()
                self.properties[2]["gridProperties"]["rowCount"] = 10000

            def read_grading_tables(self, source, ranges):
                result = super().read_grading_tables(source, ranges)
                sheet = next(s for s in result["sheets"] if s["properties"]["title"] == "Answers")
                sheet["data"].insert(0, {"startRow": 9999, "rowData": [{"values": [
                    {"formattedValue": v} for v in ["one", "last", "DRAFT", "Final row writing", "", "", "true"]]}]})
                return result
        result = project(_tables(SparseReader(), SOURCE))
        self.assertEqual([answer["question_id"] for answer in result["submissions"][0]["answers"]], ["q1", "last"])

    def test_holds_preserve_writing_but_never_show_numeric_grade(self):
        values = tables(); values["Submissions"][0]["hold_reason"] = "Assignment matching review"
        result = project(values)
        self.assertIsNone(result["submissions"][0]["content_score"])
        self.assertEqual(result["submissions"][0]["answers"][0]["response"], "Fictional writing")

    def test_score_uses_unrounded_cell_value_not_display_format(self):
        class NumericReader(Reader):
            def read_grading_tables(self, source, ranges):
                result = super().read_grading_tables(source, ranges)
                sheet = next(s for s in result["sheets"] if s["properties"]["title"] == "Submissions")
                cell = sheet["data"][0]["rowData"][1]["values"][9]
                cell.update(formattedValue="4.62", effectiveValue={"numberValue": 60 / 13})
                return result
        result = project(_tables(NumericReader(), SOURCE))
        self.assertEqual(result["submissions"][0]["content_score"], 60 / 13)

    def test_identity_unverified_and_superseded_excluded_from_counts(self):
        for status in ("identity_unverified", "superseded"):
            values = tables(); values["Submissions"][0]["status"] = status
            result = project(values)
            self.assertEqual(len(result["submissions"]), 1)
            self.assertFalse(result["submissions"][0]["included_in_counts"])
            self.assertEqual(result["summaries"][0]["submitted_students"], 0)
            self.assertIsNone(result["submissions"][0]["content_score"])

    def test_unique_students_not_number_of_artifacts(self):
        values = tables(); second = {**values["Submissions"][0], "record_id": "two"}; values["Submissions"].append(second)
        result = project(values)
        self.assertEqual(result["summaries"][0]["submitted_students"], 1)
        self.assertEqual(result["summaries"][0]["submission_records"], 2)

    def test_roster_identity_counts_without_inventing_ic_id(self):
        values = tables(); row = values["Submissions"][0]
        row.update(student_id="", learner_key="fictional-roster:section-a:alex", status="assessed")
        result = project(values)
        self.assertEqual(result["summaries"][0]["submitted_students"], 1)
        self.assertEqual(result["submissions"][0]["student_id"], "")
        values = tables(); values["Submissions"][0].update(student_id="", learner_key="ambiguous", status="identity_unverified")
        self.assertEqual(project(values)["summaries"][0]["submitted_students"], 0)

    def test_explicit_roster_to_ic_identity_link_does_not_double_count(self):
        values = tables(); row = values["Submissions"][0]
        row.update(learner_key="fictional-roster:section-a:alex", status="assessed")
        values["Submissions"].append({**row, "record_id": "earlier", "student_id": ""})
        result = project(values)
        self.assertEqual(result["summaries"][0]["submitted_students"], 1)
        self.assertEqual(result["submissions"][1]["student_id"], "")

    def test_duplicate_records_and_summary_scopes_rejected(self):
        for tab in ("Assignments", "Submissions", "Class summaries"):
            values = tables(); values[tab].append(dict(values[tab][0]))
            with self.assertRaisesRegex(ConnectionError, "grading_record_conflict"):
                project(values)

    def test_unknown_answer_record_or_assignment_rejected(self):
        for tab, key in (("Answers", "record_id"), ("Submissions", "assignment_key")):
            values = tables(); values[tab][0][key] = "unknown"
            with self.assertRaises(ConnectionError):
                project(values)

    def test_scores_must_be_finite_and_between_zero_and_ten(self):
        for value in ("NaN", "Infinity", "11", "-1", "=2+3"):
            values = tables(); values["Submissions"][0]["content_score"] = value
            with self.assertRaisesRegex(ConnectionError, "grading_score_invalid"):
                project(values)

    def test_headers_and_oversize_grids_fail_without_truncating(self):
        reader = Reader(); reader.properties[0]["gridProperties"]["rowCount"] = 10001
        with self.assertRaisesRegex(ConnectionError, "grading_workbook_layout_required"):
            _tables(reader, SOURCE)
        reader = Reader(); reader.values["Assignments"][0][0] = "wrong"
        with self.assertRaisesRegex(ConnectionError, "grading_workbook_headers_required"):
            _tables(reader, SOURCE)

    def test_authentication_before_runtime_token_or_private_read(self):
        with patch('school_workflow_hub.adapters.grading_records.authorize', side_effect=ConnectionError('sign_in_required')), patch('school_workflow_hub.adapters.grading_records.service_token') as token:
            with self.assertRaisesRegex(ConnectionError, 'sign_in_required'):
                read_records('bad', CONFIG)
            token.assert_not_called()

    def test_safe_sheets_diagnostics_reach_authenticated_http_response(self):
        capture = {}
        def start(status, headers): capture.update(status=status, headers=dict(headers))
        for code in ("authentication_required", "access_denied", "source_not_found",
                     "source_unavailable", "source_rate_limited", "source_transient_failure",
                     "response_limit", "invalid_response"):
            with self.subTest(code=code), patch.dict('os.environ', CONFIG), \
                    patch('school_workflow_hub.adapters.grading_records.authorize'), \
                    patch('school_workflow_hub.adapters.grading_records.service_token', return_value='fictional-token'), \
                    patch('school_workflow_hub.adapters.google_sheets.read_json', side_effect=SourceReadError(code)):
                body = b''.join(application({'PATH_INFO': '/api/grading-records', 'REQUEST_METHOD': 'GET',
                                            'HTTP_X_GOOG_IAP_JWT_ASSERTION': 'fictional-assertion'}, start))
                self.assertEqual(capture['status'], '503 Service Unavailable')
                self.assertEqual(capture['headers']['Cache-Control'], 'no-store')
                self.assertEqual(json.loads(body), {'error': 'grading_records_' + code, 'external_writes': 0})

    def test_unknown_sheets_errors_and_general_errors_do_not_expose_details(self):
        private = 'access_denied: fictional student; Bearer fictional-secret; https://example.invalid/private'
        for error in (SheetsReadError(private), RuntimeError(private)):
            with self.subTest(kind=type(error).__name__), \
                    patch('school_workflow_hub.adapters.grading_records.authorize'), \
                    patch('school_workflow_hub.adapters.grading_records.service_token', return_value='fictional-token'), \
                    patch('school_workflow_hub.adapters.grading_records.SheetsReader', side_effect=error):
                with self.assertRaises(ConnectionError) as caught:
                    read_records('fictional-assertion', CONFIG)
                self.assertEqual(str(caught.exception), 'grading_records_unavailable')
                self.assertTrue(caught.exception.__suppress_context__)

    def test_bulk_sheet_failure_keeps_diagnostic_without_partial_records(self):
        class FailingReader(Reader):
            def read_grading_tables(self, source, ranges):
                raise SheetsReadError('access_denied')
        with patch('school_workflow_hub.adapters.grading_records.authorize'), \
                patch('school_workflow_hub.adapters.grading_records.service_token', return_value='fictional-token'), \
                patch('school_workflow_hub.adapters.grading_records.SheetsReader', return_value=FailingReader()), \
                patch('school_workflow_hub.adapters.grading_records.project') as project_records:
            with self.assertRaisesRegex(ConnectionError, '^grading_records_access_denied$'):
                read_records('fictional-assertion', CONFIG)
            project_records.assert_not_called()

    def test_metadata_is_complete_unique_and_bounded_before_bulk_read(self):
        changes = [lambda r: r.properties.pop(),
                   lambda r: r.properties.append(copy.deepcopy(r.properties[0])),
                   lambda r: r.properties[1].update(title=r.properties[0]["title"]),
                   lambda r: r.properties[1].update(sheetId=r.properties[0]["sheetId"]),
                   lambda r: r.properties[0].update(gridProperties=None)]
        for key, value in (("rowCount", 0), ("rowCount", 10001), ("columnCount", 2), ("columnCount", 41)):
            changes.append(lambda r, k=key, v=value: r.properties[0]["gridProperties"].update({k: v}))
        for change in changes:
            reader = Reader(); change(reader)
            with self.subTest(change=change), self.assertRaises(ConnectionError):
                _tables(reader, SOURCE)
            self.assertEqual(reader.calls, [()])

    def test_bulk_response_missing_duplicate_or_reidentified_tab_fails_closed(self):
        changes = [lambda data: data["sheets"].pop(),
                   lambda data: data["sheets"].append(copy.deepcopy(data["sheets"][0])),
                   lambda data: data["sheets"][0]["properties"].update(sheetId=100),
                   lambda data: data["sheets"][1]["properties"].update(title="Assignments"),
                   lambda data: data["sheets"][1].update(properties=None)]
        for change in changes:
            reader = Reader(); original = reader.read_grading_tables
            def read(source, ranges):
                data = original(source, ranges); change(data); return data
            reader.read_grading_tables = read
            with self.subTest(change=change), self.assertRaises(ConnectionError):
                _tables(reader, SOURCE)

    def test_bulk_rows_remain_bounded_and_nonoverlapping(self):
        changes = [lambda sheet: sheet["data"].append(copy.deepcopy(sheet["data"][0])),
                   lambda sheet: sheet["data"][0].update(startRow=700),
                   lambda sheet: sheet["data"][0].update(startColumn=1),
                   lambda sheet: sheet["data"][0]["rowData"][0].update(values=[{}] * 27),
                   lambda sheet: sheet["data"][0].update(rowData="not rows")]
        for change in changes:
            reader = Reader(); original = reader.read_grading_tables
            def read(source, ranges):
                data = original(source, ranges); change(data["sheets"][0]); return data
            reader.read_grading_tables = read
            with self.subTest(change=change), self.assertRaisesRegex(ConnectionError, "grading_workbook_layout_required"):
                _tables(reader, SOURCE)

    def test_closing_metadata_change_rejects_full_read(self):
        class ChangedReader(Reader):
            def read(self, source, ranges=()):
                response = super().read(source, ranges)
                if len(self.calls) == 3:
                    response["sheets"][0]["properties"]["gridProperties"]["rowCount"] += 1
                return response
        with self.assertRaisesRegex(ConnectionError, "grading_workbook_changed"):
            _tables(ChangedReader(), SOURCE)

    def test_http_read_only_no_store_and_no_private_errors(self):
        capture = {}
        def start(status, headers): capture.update(status=status, headers=dict(headers))
        with patch('school_workflow_hub.adapters.grading_records.read_records', return_value=project(tables())):
            body = b''.join(application({'PATH_INFO': '/api/grading-records', 'REQUEST_METHOD': 'GET'}, start))
        self.assertEqual(capture['status'], '200 OK')
        self.assertEqual(capture['headers']['Cache-Control'], 'no-store')
        self.assertEqual(json.loads(body)['external_writes'], 0)
        with patch('school_workflow_hub.adapters.grading_records.read_records', side_effect=RuntimeError('private student data')):
            body = b''.join(application({'PATH_INFO': '/api/grading-records', 'REQUEST_METHOD': 'GET'}, start))
        self.assertNotIn(b'private student data', body)
        for method, query, expected in [('POST', '', '405'), ('GET', 'sheet_id=other', '400')]:
            application({'PATH_INFO': '/api/grading-records', 'REQUEST_METHOD': method, 'QUERY_STRING': query}, start)
            self.assertTrue(capture['status'].startswith(expected))


if __name__ == '__main__': unittest.main()
