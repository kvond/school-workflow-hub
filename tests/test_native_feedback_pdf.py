"""Expanded student presentations preserve saved source evidence and history."""
import io
import unittest
from copy import deepcopy
from pypdf import PdfReader
from hub_v2 import feedback_pdf
from hub_v2.native_feedback import Held
from tests.test_native_feedback import inputs, report


def saved(course='anatomy'):
    value = inputs()
    value.update(report=report(), created_at='2026-10-06T00:04:00+00:00')
    value['scope']['class'] = course
    return value


class StudentPresentationTests(unittest.TestCase):
    def test_both_courses_show_saved_evidence_and_leave_original_unchanged(self):
        for course in ('anatomy', 'forensics'):
            with self.subTest(course=course):
                value = saved(course); original = deepcopy(value)
                legacy = feedback_pdf.render(value)
                raw = feedback_pdf.render_student_presentation(value)
                pages = PdfReader(io.BytesIO(raw)).pages
                text = '\n'.join(page.extract_text() for page in pages)
                self.assertGreaterEqual(len(pages), 2)
                self.assertIn('Carbon comes from the air.', text)
                self.assertIn('A mechanism is still needed.', text)
                self.assertIn('WHAT THIS SHOWS', text)
                self.assertIn('[X] Developing', text)
                self.assertIn('2026-10-05', text)
                self.assertIn(feedback_pdf.HAND_BACK, text)
                self.assertEqual(text.count(value['report']['question']), 1)
                self.assertNotIn('Private concern', text)
                self.assertNotIn('Private science-writing', text)
                self.assertNotIn('YOUR GROWTH', text)
                self.assertNotIn('Names an idea', text)
                self.assertNotIn('not for submission', text)
                self.assertIn('black and white', text)
                self.assertEqual(value, original)
                self.assertEqual(feedback_pdf.render(value), legacy)
                self.assertEqual(feedback_pdf.render_student_presentation(value), raw)

    def test_maximum_content_paginated_without_dropping_any_aspect(self):
        value = saved('forensics')
        value['report']['student_summary'] = 'Summary ' * 56
        value['report']['question'] = 'Which evidence supports your explanation ' + 'and why ' * 41 + '?'
        value['report']['aspects'] = [dict(
            question_id='Q%d' % index, name=('Aspect%d ' % index) * 17,
            current_level='Proficient', quote=('Quote%d ' % index) * 25,
            explanation=('Explanation%d ' % index) * 36) for index in range(4)]
        pages = PdfReader(io.BytesIO(feedback_pdf.render_student_presentation(value))).pages
        text = '\n'.join(page.extract_text() for page in pages)
        for index in range(4):
            self.assertIn('Quote%d' % index, text)
            self.assertIn('Explanation%d' % index, text)
        self.assertIn('How I can improve my responses next time', text)
        self.assertGreater(len(pages), 2)

    def test_unavailable_is_not_rated_or_quoted(self):
        value = saved()
        value['report']['aspects'][0].update(current_level='Evidence unavailable', quote='')
        text = '\n'.join(page.extract_text() for page in PdfReader(io.BytesIO(
            feedback_pdf.render_student_presentation(value))).pages)
        self.assertNotIn('[X]', text)
        self.assertIn('Evidence unavailable', text)
        self.assertIn('not a zero', text)

    def test_exact_course_binding_required(self):
        for course in ('biology', 'A&P', '', None):
            with self.assertRaises(Held):
                feedback_pdf.render_student_presentation(saved(course))

    def test_only_explicit_supported_version_dispatches_and_bad_versions_hold(self):
        value = saved()
        value['student_presentation_version'] = feedback_pdf.STUDENT_PRESENTATION_RENDER_VERSION
        self.assertEqual(feedback_pdf.render(value), feedback_pdf.render_student_presentation(value))
        value['student_presentation_version'] = 'unrecognized-version'
        with self.assertRaises(Held):
            feedback_pdf.render(value)
