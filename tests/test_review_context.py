"""Teacher review context on fictional sources; no provider or scoring calls."""
from copy import deepcopy
from unittest.mock import patch
import unittest

from hub_v2 import app, completion, google, review_context as rc, work
from test_hub_v2 import fixture, DIRECT, DECK, ROSTER, response, call

FORM = DIRECT.split('/')[5]
SOURCE = 'FICTIONAL_STUDENT_SOURCE_123'
TEACHER = DECK.split('/')[5]


def row(**extra):
    return {'first': 'Alex', 'last': 'Rivera', 'section': 'A2 Fictional', 'matched': True,
            'source': 'folder', 'matched_by': 'file name', 'submitted_at': '2026-09-22T14:00:00Z',
            'files': [{'id': SOURCE, 'name': 'Original answers', 'mime': google.PPTX,
                       'url': 'https://drive.google.com/open?id=' + SOURCE}], **extra}


def found(rows=None, **extra):
    form = {'form_id': FORM, 'label': 'Reasoning', 'students': rows if rows is not None else [row()],
            'late': [], 'missing': [], 'excused': [], 'elsewhere': [], 'problem': None}
    return {'class_key': 'forensics', 'week': 'Wk 04',
            'lessons': [{'topic': 'Fictional topic', 'forms': [form]}],
            'problems': [], 'other_files': [], 'folder_note': None, **extra}


def scope():
    return rc.scope(fixture(), {}, 'forensics', 'Wk 04', FORM)


def metadata(ident, **extra):
    return {'id': ident, 'mimeType': google.PPTX, 'trashed': False,
            'version': '7', 'modifiedTime': '2026-10-11T10:00:00Z', **extra}


def assessment():
    return {'note': 'Slides 2, 19: A response or choice is ambiguous.',
            'template_id': TEACHER, 'notes_score': 8.3, 'revision_score': None,
            'scoring_basis': 'found_codes', 'missing_code_warning': 'Missing codes are advisory.'}


def complete_roster():
    return ({cls: [{'first': 'Alex', 'last': 'Rivera', 'section': section, 'class_key': cls, 'order': 'header'}
                   for section in sections] for cls, sections in work.roster.SECTIONS.items()}, [])


class ReviewContextTests(unittest.TestCase):
    def test_exact_scope_rejects_foreign_week_class_form_and_duplicate_current_forms(self):
        self.assertEqual(scope()['lesson'], 'Reasoning')
        for cls, week, fid in [('biology', 'Wk 04', 'FOREIGN_FORM_123'),
                               ('forensics', 'Wk 03', FORM), ('forensics', 'Wk 04', 'FOREIGN_FORM_123')]:
            with self.assertRaises(ValueError):
                rc.scope(fixture(), {}, cls, week, fid)
        agenda = fixture()
        lessons = agenda.lessons('forensics', 'Wk 04')
        lessons[0]['forms'].append(deepcopy(lessons[0]['forms'][0]))
        with patch.object(agenda, 'lessons', return_value=lessons), self.assertRaises(ValueError):
            rc.scope(agenda, {}, 'forensics', 'Wk 04', FORM)

    def test_full_history_reason_identity_source_and_unknown_slides_are_preserved(self):
        problem = 'Original Form event is unavailable.\n' + 'Detailed original history. ' * 30
        original = found([row(selection_problem=problem)])
        before = deepcopy(original)
        detail = rc.build(scope(), original)['notices'][0]
        self.assertEqual(detail['reason'], problem)
        self.assertEqual(detail['student'], {'first': 'Alex', 'last': 'Rivera', 'section': 'A2 Fictional', 'verified': True})
        self.assertEqual(detail['sources'][0]['file_id'], SOURCE)
        self.assertEqual(detail['slides'][0], {'file_id': SOURCE, 'numbers': [], 'verified': False})
        self.assertIn('current revision', detail['next_action'])
        self.assertEqual(original, before)

    def test_unknown_student_is_not_inferred_from_name_filename_email_or_reason_slides(self):
        candidate = row(matched=False, email='s.alex.rivera@fictional.example',
                        selection_problem='Slide 19: unknown owner')
        detail = rc.build(scope(), found([candidate]))['notices'][0]
        self.assertEqual(detail['student'], {'first': '', 'last': '', 'section': '', 'verified': False})
        self.assertEqual(detail['affected_scope'], 'student')
        self.assertEqual(detail['slides'][0]['numbers'], [])
        self.assertIn('student’s name and class section', detail['next_action'])

    def test_canonical_links_reject_credentials_foreign_hosts_prefix_collisions_and_script(self):
        good = 'https://docs.google.com/presentation/d/' + SOURCE + '/edit#slide=id.unverified'
        self.assertEqual(rc.source_link(good, SOURCE)['url'], good.split('#')[0])
        for url in ['javascript:alert(1)', 'https://docs.google.com.evil.example/presentation/d/' + SOURCE + '/edit',
                    'https://teacher:secret@docs.google.com/presentation/d/' + SOURCE + '/edit',
                    'https://docs.google.com:443/presentation/d/' + SOURCE + '/edit',
                    good.replace(SOURCE, SOURCE + '_foreign'), 'https://drive.google.com/open?id=' + SOURCE + '&id=OTHER_SOURCE_123',
                    good + '\n', 'http://docs.google.com/presentation/d/' + SOURCE + '/edit']:
            self.assertIsNone(rc.source_link(url, SOURCE), url)
        self.assertIsNotNone(rc.source_link('https://docs.google.com/forms/d/' + FORM + '/edit', FORM))
        self.assertIsNotNone(rc.source_link('https://drive.google.com/drive/folders/' + SOURCE, SOURCE))

    def test_lesson_level_folder_failure_is_not_waiting_or_a_named_student(self):
        result = rc.build(scope(), found([], folder_note='Current turn-in folder could not be read.'))
        self.assertEqual(result['status'], 'review')
        self.assertEqual(result['notices'][0]['affected_scope'], 'lesson')
        self.assertFalse(result['notices'][0]['student']['verified'])
        self.assertTrue(result['notices'][0]['sources'])
        self.assertNotIn('Waiting for submissions', ' '.join(result['limitations']))

    def test_empty_photosynthesis_is_waiting_not_code_repair_or_zero(self):
        current = {**scope(), 'class_key': 'biology', 'class_name': 'Biology', 'lesson': 'Photosynthesis'}
        result = rc.build(current, {**found([]), 'class_key': 'biology'})
        self.assertEqual((result['status'], result['notices']), ('waiting', []))
        self.assertIn('before an original is received', result['limitations'][0])
        self.assertFalse(result['source_selection']['assessment_performed'])

    def test_found_code_warning_and_zero_revision_denominator_do_not_create_review(self):
        source = found()
        result = rc.build(scope(), source, {SOURCE: {'notes_score': 10, 'revision_score': None,
            'counts': {'DRAFT': {'required': 0}}, 'missing_code_warning': 'Missing embedded codes'}})
        self.assertEqual(result['notices'], [])
        self.assertEqual(result['status'], 'not_assessed')
        self.assertIn('Other responses have not been checked here', result['limitations'][0])

    def test_explicit_exempt_source_issue_does_not_request_teacher_action(self):
        for key in [{'exempt': True}, {'flags': {'exempt': True}}]:
            result = rc.build(scope(), found([row(selection_problem='Unordered original history', **key)]))
            self.assertEqual(result['notices'], [])

    def test_other_lesson_requires_current_form_and_verified_roster_identity(self):
        source = found([])
        form = source['lessons'][0]['forms'][0]
        form['missing'] = [{'first': 'Alex', 'last': 'Rivera', 'section': 'A2 Fictional'}]
        form['elsewhere'] = [{'who': 'Rivera, Alex', 'section': 'A2 Fictional', 'accepted_intake': False,
            'source_form_id': FORM, 'assignment_form_id': 'DIFFERENT_FORM_123', 'to': 'Wk 01 · Different lesson',
            'url': 'https://drive.google.com/open?id=' + SOURCE, 'file': 'Actual different lesson original'}]
        detail = rc.build(scope(), source)['notices'][0]
        self.assertIn('Wk 01 · Different lesson', detail['reason'])
        self.assertTrue(detail['student']['verified'])
        form['elsewhere'][0]['source_form_id'] = None
        form['elsewhere'][0]['who'] = 'UNVERIFIED NAME FROM FILENAME'
        detail = rc.build(scope(), source)['notices'][0]
        self.assertNotIn('Different lesson', detail['reason'])
        self.assertFalse(detail['student']['verified'])


    def other_current_lesson(self):
        source = found([])
        current = source['lessons'][0]['forms'][0]
        current['missing'] = [row(files=[])]
        current['elsewhere'] = [{'who': 'Rivera, Alex', 'section': 'A2 Fictional', 'accepted_intake': False,
            'source_form_id': FORM, 'assignment_form_id': 'FICTIONAL_OTHER_CURRENT_FORM_123',
            'to': 'An untrusted display title', 'file': 'Original retained answers',
            'url': 'https://drive.google.com/open?id=' + SOURCE}]
        source['lessons'][0]['forms'].append({'form_id': 'FICTIONAL_OTHER_CURRENT_FORM_123',
            'label': 'Observation', 'students': [], 'late': [], 'missing': [], 'excused': [], 'elsewhere': []})
        return source

    def test_exact_known_other_current_form_is_information_with_links_and_no_action_or_grade_claim(self):
        source = self.other_current_lesson(); before = deepcopy(source)
        result = rc.build(scope(), source)
        self.assertEqual(result['notices'], [])
        item = result['matched_other_lessons'][0]
        self.assertTrue(item['student']['verified'])
        self.assertEqual(item['target'], {'class_key': 'forensics', 'class_name': 'Forensics', 'week': 'Wk 04',
            'form_id': 'FICTIONAL_OTHER_CURRENT_FORM_123', 'lesson': 'Observation',
            'url': 'https://docs.google.com/forms/d/FICTIONAL_OTHER_CURRENT_FORM_123/edit'})
        self.assertEqual(item['sources'][0]['file_id'], SOURCE)
        self.assertEqual(item['slides'][0]['numbers'], [])
        self.assertNotIn('next_action', item)
        self.assertNotIn('Saved', item['reason']); self.assertNotIn('graded', item['reason'])
        self.assertNotIn('untrusted', item['reason'])
        self.assertFalse(result['source_selection']['assessment_performed'])
        self.assertEqual(source, before)

    def test_unknown_duplicate_foreign_or_unavailable_other_target_remains_actionable(self):
        for change in ('unknown', 'duplicate', 'foreign_class', 'foreign_week', 'unavailable', 'foreign_source'):
            source = self.other_current_lesson()
            current, target = source['lessons'][0]['forms']
            if change == 'unknown': target['form_id'] = 'FICTIONAL_UNKNOWN_FORM_123'
            elif change == 'duplicate': source['lessons'][0]['forms'].append(deepcopy(target))
            elif change == 'foreign_class': source['class_key'] = 'biology'
            elif change == 'foreign_week': source['week'] = 'Wk 05'
            elif change == 'unavailable': target['problem'] = 'Target Form could not be read.'
            else: current['elsewhere'][0]['source_form_id'] = 'FICTIONAL_FOREIGN_FORM_123'
            if change.startswith('foreign_') and change != 'foreign_source':
                with self.assertRaises(ValueError): rc.build(scope(), source)
            else:
                result = rc.build(scope(), source)
                self.assertEqual(result['matched_other_lessons'], [])
                self.assertIn('next_action', result['notices'][0])

    def test_unverified_or_ambiguous_person_and_unsafe_source_keep_review_not_information(self):
        for change in ('unknown', 'namesake', 'unsafe_link'):
            source = self.other_current_lesson(); current = source['lessons'][0]['forms'][0]
            if change == 'unknown': current['missing'] = []
            elif change == 'namesake': current['students'] = [row(files=[], email='other@fictional.example')]
            else: current['elsewhere'][0]['url'] = 'https://foreign.example/' + SOURCE
            result = rc.build(scope(), source)
            self.assertEqual(result['matched_other_lessons'], [])
            self.assertIn('next_action', result['notices'][0])

    def test_other_lesson_information_does_not_hide_real_history_assessment_or_grade_candidates(self):
        source = self.other_current_lesson(); current = source['lessons'][0]['forms'][0]
        current['students'] = [row(selection_problem='Original history still needs review')]
        current['missing'] = []
        before = completion.recheck_candidates(current)
        result = rc.build(scope(), source)
        self.assertEqual(len(result['matched_other_lessons']), 1)
        self.assertEqual([n['reason'] for n in result['notices']], ['Original history still needs review'])
        self.assertEqual(completion.recheck_candidates(current), before)
        current['students'] = [row()]
        result = rc.build(scope(), source, {SOURCE: assessment()})
        self.assertEqual(len(result['matched_other_lessons']), 1)
        self.assertEqual(result['notices'][0]['issue_kind'], 'assessment')
        self.assertEqual(result['notices'][0]['slides'][0]['numbers'], [2, 19])

    def test_known_other_lesson_preserves_exempt_and_accepted_intake_suppression(self):
        for change in ('exempt', 'accepted'):
            source = self.other_current_lesson(); current = source['lessons'][0]['forms'][0]
            if change == 'exempt': current['missing'][0]['exempt'] = True
            else: current['elsewhere'][0]['accepted_intake'] = True
            result = rc.build(scope(), source)
            self.assertEqual(result['notices'], [])
            self.assertEqual(result['matched_other_lessons'], [])

    def test_week01_case_display_is_nonaction_without_excluding_lab_safety(self):
        current = {**scope(), 'week': 'Wk 01', 'lesson': 'Work a case'}
        source = {**found([row(selection_problem='Case source needs original match')]), 'week': 'Wk 01'}
        result = rc.build(current, source)
        self.assertEqual((result['status'], result['notices']), ('excluded', []))
        self.assertIn('does not certify a deployed grading exclusion', result['limitations'][0])
        self.assertEqual(rc.build({**current, 'lesson': 'Lab Safety'}, source)['status'], 'review')

    def cached(self, *, md=None, resolution=None, cache=None):
        key = (SOURCE, metadata(SOURCE)['modifiedTime'], '7')
        template = (TEACHER, metadata(TEACHER)['modifiedTime'], '7')
        default = {key: {'template': template, 'student_coding_required': False, 'result': assessment()}}
        sources = {'decks': [{'id': TEACHER}], 'folders': [{'id': 'FICTIONALFOLDER123', 'deck_ids': [TEACHER]}], 'problem': None}
        with patch.dict(completion._results, default if cache is None else cache, clear=True), \
                patch.object(completion, 'lesson_sources', side_effect=resolution or [sources, deepcopy(sources)]), \
                patch.object(google, 'drive_file', side_effect=md or (lambda token, fid, fields: metadata(fid))), \
                patch.object(completion, 'score_file', side_effect=AssertionError('no scoring')), \
                patch.object(completion, 'assess', side_effect=AssertionError('no assessment')), \
                patch.object(google, 'deck_bytes', side_effect=AssertionError('no source byte read')):
            from test_completion_readiness import single_assignment_agenda
            return rc.cached_reviews('fictional', single_assignment_agenda(), 'forensics', 'Wk 04', FORM, [row()])

    def test_cached_response_slides_require_exact_source_template_and_folder_bookends(self):
        cached, limitations = self.cached()
        self.assertEqual(limitations, [])
        detail = rc.build(scope(), found(), cached)['notices'][0]
        self.assertEqual(detail['slides'][0]['numbers'], [2, 19])
        self.assertTrue(detail['slides'][0]['verified'])
        self.assertEqual(detail['source_basis']['kind'], 'cached_assessment_verified')

    def test_stale_source_teacher_or_unavailable_folder_never_reuses_old_note(self):
        for ident in (SOURCE, TEACHER):
            cached, limitations = self.cached(md=lambda token, fid, fields: metadata(fid, version='8') if fid == ident else metadata(fid))
            self.assertEqual(cached, {})
            self.assertTrue(limitations)
        cached, limitations = self.cached(resolution=[{'decks': [], 'problem': 'Actual Agenda folder is unreadable'}])
        self.assertEqual(cached, {})
        self.assertIn('unreadable', limitations[0])

    def test_same_version_source_drift_and_teacher_folder_change_discard_details(self):
        calls = {}
        def drift(token, fid, fields):
            calls[fid] = calls.get(fid, 0) + 1
            return metadata(fid, md5Checksum='changed' if fid == SOURCE and calls[fid] > 1 else 'original')
        self.assertEqual(self.cached(md=drift)[0], {})
        first = {'decks': [{'id': TEACHER}], 'folders': [], 'problem': None}
        self.assertEqual(self.cached(resolution=[first, {**first, 'decks': []}])[0], {})

    def test_cache_absence_does_not_read_bytes_grade_or_claim_all_caught_up(self):
        with patch.object(google, 'drive_file', side_effect=AssertionError('no cache metadata needed')):
            cached, _ = self.cached(cache={})
        result = rc.build(scope(), found(), cached)
        self.assertEqual((result['status'], result['notices']), ('not_assessed', []))
        self.assertFalse(result['source_selection']['assessment_performed'])

    def test_actual_score_result_gets_context_once_with_numeric_fields_unchanged(self):
        score = {**assessment(), 'file_id': SOURCE, 'file_updated': 'unchanged'}
        original = deepcopy(score)
        with patch.object(completion, 'score_file', return_value=score) as scorer:
            result = app._score_with_context('fictional', fixture(), 'forensics', 'Wk 04', FORM, SOURCE, found())
        scorer.assert_called_once()
        self.assertEqual({k: result[k] for k in original}, original)
        self.assertEqual(result['review_context']['notice']['slides'][0]['numbers'], [2, 19])
        self.assertEqual(result['review_context']['scope']['class_name'], 'Forensics')
        self.assertEqual(score, original)

    def test_exact_form_url_label_uses_its_agenda_topic_without_foreign_title_guess(self):
        source = found()
        form = source['lessons'][0]['forms'][0]
        score = {**assessment(), 'file_id': SOURCE}
        form['label'] = DIRECT + '#responses'
        result = rc.for_score(fixture(), 'forensics', 'Wk 04', FORM, SOURCE, source, score)
        self.assertEqual(result['review_context']['scope']['lesson'], 'Fictional topic')
        self.assertEqual(result['review_context']['scope']['form_id'], FORM)
        self.assertEqual(result['notes_score'], score['notes_score'])
        form['label'] = DIRECT.replace(FORM, 'DIFFERENT_FORM_123')
        result = rc.for_score(fixture(), 'forensics', 'Wk 04', FORM, SOURCE, source, score)
        self.assertEqual(result['review_context']['scope']['lesson'], form['label'])

    def test_api_exact_route_rejects_extras_and_uses_fresh_discovery_only(self):
        query = {'class': ['forensics'], 'week': ['Wk 04'], 'form': [FORM]}
        with patch.object(google, 'service_token', return_value='fictional'), \
                patch.object(app, 'load_agenda', return_value=fixture()) as agenda_read, \
                patch.object(work.roster, 'load', return_value=complete_roster()), \
                patch.object(work, 'lesson_work', return_value=found()) as discovery, \
                patch.object(completion, 'score_file', side_effect=AssertionError('no scoring')), \
                patch.object(completion, 'assignment_readiness', side_effect=AssertionError('no byte readiness')), \
                patch.dict(completion._results, {}, clear=True):
            result = app.api('api/review-context', query, {})
            self.assertEqual(result['schema'], rc.SCHEMA)
            self.assertEqual(agenda_read.call_count, 2)
            self.assertTrue(all(c.args == ('fictional', {}) and c.kwargs == {'fresh': True} for c in agenda_read.call_args_list))
            discovery.assert_called_once_with('fictional', {}, agenda_read.return_value, 'forensics', 'Wk 04',
                                              review_only=True, roster_snapshot=complete_roster())
            with self.assertRaises(ValueError):
                app.api('api/review-context', {**query, 'file': [SOURCE]}, {})
            with self.assertRaises(ValueError):
                app.api('api/review-context', {**query, 'week': ['Wk 03']}, {})
            self.assertEqual(discovery.call_count, 1)

    def test_real_work_review_only_skips_grade_readiness_combined_binding_and_byte_reads(self):
        students, _ = work.roster.parse_tab(ROSTER, 'forensics', 'A2 Forensics')
        config = {'HUB_READER_SERVICE_ACCOUNT': 'reader@fictional.example', 'HUB_SCHOOL_EMAIL_DOMAINS': 'school.example'}
        responses = [response('s.ana.rivera@school.example', '2026-09-22T14:00:00Z', (SOURCE, 'Deck - Ana Rivera'))]
        from hub_v2 import combined_assessment
        with patch.object(work.roster, 'load', return_value=({'forensics': students}, [])), \
                patch.object(google, 'form_responses', side_effect=lambda token, fid: responses if fid == FORM else []), \
                patch.object(google, 'drive_file', return_value={'id': SOURCE, 'name': 'Deck', 'trashed': False, 'mimeType': completion.SLIDES}), \
                patch.object(completion, 'assignment_readiness', side_effect=AssertionError('no readiness bytes')), \
                patch.object(combined_assessment, 'bind_form', side_effect=AssertionError('no component parse')), \
                patch.object(google, 'deck_bytes', side_effect=AssertionError('no bytes')), \
                patch.object(completion, 'score_file', side_effect=AssertionError('no scoring')):
            result = work.lesson_work('fictional', config, fixture(), 'forensics', 'Wk 04', review_only=True)
        self.assertEqual(result['lessons'][0]['forms'][0]['students'][0]['files'][0]['id'], SOURCE)
        self.assertTrue(all('grade_readiness' not in form for lesson in result['lessons'] for form in lesson['forms']))

    def test_application_requires_teacher_sign_in_and_rejects_posts_before_source_access(self):
        with patch.object(google, 'service_token') as token, patch.object(work, 'lesson_work') as source:
            self.assertEqual(call('/v2/api/review-context', query='class=forensics&week=Wk+04&form=' + FORM)[0], '401 Unauthorized')
            self.assertEqual(call('/v2/api/review-context', method='POST', assertion='signed')[0], '405 Method Not Allowed')
        token.assert_not_called()
        source.assert_not_called()

    def test_authenticated_application_read_never_scores_models_writes_or_requests_write_identity(self):
        with patch.object(google, 'verify_teacher', return_value='teacher@fictional.example'), \
                patch.object(google, 'service_token', return_value='fictional') as token, \
                patch.object(app, 'load_agenda', return_value=fixture()), \
                patch.object(work.roster, 'load', return_value=complete_roster()), \
                patch.object(work, 'lesson_work', return_value=found([row(selection_problem='Current original history is unordered')])), \
                patch.object(completion, 'score_file', side_effect=AssertionError('no scoring')) as score, \
                patch.object(completion, 'assess', side_effect=AssertionError('no assessment')) as assess, \
                patch.object(app.ledger, 'record', side_effect=AssertionError('no ledger writes')) as record, \
                patch.object(app.native_feedback, 'generate', side_effect=AssertionError('no model calls')) as model, \
                patch.object(google, 'append_rows', side_effect=AssertionError('no append')) as append, \
                patch.object(google, 'update_cells', side_effect=AssertionError('no update')) as update:
            status, headers, body = call('/v2/api/review-context', query='class=forensics&week=Wk+04&form=' + FORM,
                                        assertion='signed')
        self.assertEqual(status, '200 OK')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        token.assert_called_once_with({})
        for prohibited in (score, assess, record, model, append, update):
            prohibited.assert_not_called()

    def test_18_scope_overview_uses_two_roster_agenda_reads_nine_discoveries_and_queue_two(self):
        agenda = fixture()
        catalog = {'assignments': [{'class': cls, 'class_name': agenda.classes[cls]['name'], 'week': week,
                    'form': 'FICTIONAL_FORM_%s_%s_%d' % (cls, week.replace(' ', ''), n), 'assignment': 'Actual lesson ' + str(n)}
                   for cls in agenda.classes for week in ('Wk 04', 'Wk 05', 'Wk 06') for n in (1, 2)],
                   'problems': [], 'exclusions': []}
        def lessons(cls, week):
            return [{'topic': 'Actual lesson', 'materials': [], 'forms': [{'form_id': a['form'], 'label': a['assignment']}
                     for a in catalog['assignments'] if a['class'] == cls and a['week'] == week]}]
        def discover(token, config, actual_agenda, cls, week, **kwargs):
            self.assertIs(actual_agenda, agenda)
            self.assertEqual(kwargs, {'review_only': True, 'roster_snapshot': complete_roster()})
            return {'class_key': cls, 'week': week, 'problems': [], 'other_files': [], 'folder_note': None,
                    'lessons': [{'topic': 'Actual lesson', 'forms': [{'form_id': a['form'], 'students': [], 'late': [],
                    'missing': [], 'elsewhere': [], 'excused': [], 'problem': None}
                    for a in catalog['assignments'] if a['class'] == cls and a['week'] == week]}]}
        with patch.object(google, 'service_token', return_value='fictional'), \
                patch.object(app, 'load_agenda', return_value=agenda) as agenda_load, \
                patch.object(agenda, 'lessons', side_effect=lessons), \
                patch.object(rc.morning, 'catalog', return_value=catalog), \
                patch.object(work.roster, 'load', return_value=complete_roster()) as roster_load, \
                patch.object(work, 'lesson_work', side_effect=discover) as discovery, \
                patch.object(rc, 'ThreadPoolExecutor', wraps=rc.ThreadPoolExecutor) as queue, \
                patch.object(completion, 'score_file', side_effect=AssertionError('no scoring')), \
                patch.object(completion, 'assess', side_effect=AssertionError('no assessment')), \
                patch.object(google, 'deck_bytes', side_effect=AssertionError('no bytes')), \
                patch.object(google, 'append_rows', side_effect=AssertionError('no writes')):
            result = app.api('api/review-context', {}, {})
        self.assertEqual((result['schema'], result['status'], len(result['reviews'])), (rc.OVERVIEW_SCHEMA, 'current', 18))
        self.assertEqual((roster_load.call_count, agenda_load.call_count, discovery.call_count), (2, 2, 9))
        queue.assert_called_once_with(max_workers=2)
        self.assertTrue(all(r['status'] == 'waiting' and not r['notices'] for r in result['reviews']))

    def test_initial_failed_roster_never_discovers_or_asks_to_identify_known_students(self):
        for snapshot in [({}, ['Google is rate-limiting requests. Wait a minute and try again.']), ({}, [])]:
            with patch.object(work.roster, 'load', return_value=snapshot), patch.object(work, 'lesson_work') as discovery:
                result = rc.overview('fictional', {}, fixture(), reload_agenda=fixture)
            discovery.assert_not_called()
            self.assertEqual(result['status'], 'unavailable')
            self.assertTrue(all(r['status'] == 'unavailable' and all(not n['student']['verified'] and
                                'identity decision' in n['next_action'] for n in r['notices']) for r in result['reviews']))
            self.assertNotIn('Confirm the student', str(result))

    def test_final_roster_failure_or_agenda_change_discards_all_named_details(self):
        original = fixture()
        def discover(token, config, agenda, cls, week, **kwargs):
            result = found([row(selection_problem='Current original history needs revision review')])
            result.update(class_key=cls, week=week)
            result['lessons'][0]['forms'] = [{**result['lessons'][0]['forms'][0], 'form_id': f['form_id']}
                                           for lesson in agenda.lessons(cls, week) for f in lesson['forms']]
            return result
        changed = fixture()
        changed.title += ' changed'
        cases = [(fixture(), ({}, ['Google is rate-limiting requests.'])),
                 (changed, complete_roster()),
                 (fixture(), ({**complete_roster()[0], 'forensics': []}, []))]
        for after_agenda, after_roster in cases:
            with patch.object(work.roster, 'load', side_effect=[complete_roster(), after_roster]), \
                    patch.object(work, 'lesson_work', side_effect=discover), patch.dict(completion._results, {}, clear=True):
                result = rc.overview('fictional', {}, original, reload_agenda=lambda: after_agenda)
            self.assertEqual(result['status'], 'unavailable')
            self.assertTrue(all(not n['student']['verified'] and not n['student']['first']
                                for r in result['reviews'] for n in r['notices']))
            self.assertNotIn('Current original history needs revision review', str(result))

    def test_elsewhere_uses_current_late_missing_excused_identity_and_never_name_only_collapse(self):
        item = {'who': 'Rivera, Alex', 'section': 'A2 Fictional', 'accepted_intake': False,
                'source_form_id': FORM, 'assignment_form_id': 'DIFFERENT_FORM_123', 'to': 'Another lesson',
                'url': 'https://drive.google.com/open?id=' + SOURCE}
        for location in ('students', 'late', 'missing', 'excused'):
            source = found([])
            form = source['lessons'][0]['forms'][0]
            form[location] = [row(files=[])]
            form['elsewhere'] = [item]
            detail = rc.build(scope(), source)['notices'][0]
            self.assertTrue(detail['student']['verified'], location)
            self.assertEqual(detail['affected_scope'], 'student')
        source = found([])
        form = source['lessons'][0]['forms'][0]
        form['elsewhere'] = [item]
        form['students'] = [row(files=[], email='same@fictional.example')]
        form['missing'] = [row(files=[], email='same@fictional.example')]
        self.assertTrue(rc.build(scope(), source)['notices'][0]['student']['verified'])
        for other in ['different@fictional.example', '']:
            form['missing'][0]['email'] = other
            detail = rc.build(scope(), source)['notices'][0]
            self.assertFalse(detail['student']['verified'])
            self.assertEqual(detail['affected_scope'], 'student')

    def test_scoped_provider_429_never_requests_source_repair_or_identity_decision(self):
        source = found([row(selection_problem='A source revision needs review')])
        source['lessons'][0]['forms'][0]['students'][0]['files'][0]['problem'] = 'Google is rate-limiting requests.'
        result = rc._read_found('fictional', {}, fixture(), scope(), source)
        self.assertEqual(result['status'], 'unavailable')
        detail = result['notices'][0]
        self.assertEqual(detail['affected_scope'], 'lesson')
        self.assertFalse(detail['student']['verified'])
        self.assertIn('Read again after Google allows', detail['next_action'])
        self.assertIn('No source repair or student identity decision', detail['next_action'])

    def test_shared_roster_snapshot_is_review_only_copied_and_never_reloaded_per_discovery(self):
        snapshot = complete_roster()
        before = deepcopy(snapshot)
        with patch.object(work.roster, 'load', side_effect=AssertionError('no repeated roster read')), self.assertRaises(ValueError):
            work.lesson_work('fictional', {}, fixture(), 'forensics', 'Wk 04', roster_snapshot=snapshot)
        with patch.object(work.roster, 'load', side_effect=AssertionError('no repeated roster read')), \
                patch.object(google, 'form_responses', return_value=[]), \
                patch.object(google, 'drive_file', return_value={'id': TEACHER, 'name': 'Deck', 'trashed': False, 'mimeType': completion.SLIDES}), \
                patch.object(work.roster, 'orient', side_effect=lambda rows, names: rows[0].update(first='Local copy only')), \
                patch.object(work, 'roster_check', side_effect=AssertionError('no repeated roster metadata')), \
                patch.object(completion, 'assignment_readiness', side_effect=AssertionError('no readiness bytes')):
            work.lesson_work('fictional', {'HUB_READER_SERVICE_ACCOUNT': 'reader@fictional.example'}, fixture(),
                             'forensics', 'Wk 04', review_only=True, roster_snapshot=snapshot)
        self.assertEqual(snapshot, before)

    def test_default_fresh_agenda_callback_uses_configured_spreadsheet_id(self):
        with patch.object(work.roster, 'load', return_value=complete_roster()), \
                patch.object(work, 'lesson_work', return_value=found()), \
                patch.object(rc.agenda_module, 'load', return_value=fixture()) as reload, \
                patch.dict(completion._results, {}, clear=True):
            result = rc.read('fictional', {'HUB_AGENDA_SPREADSHEET_ID': 'EXACT_SHEET_123'}, fixture(), 'forensics', 'Wk 04', FORM)
        reload.assert_called_once_with('fictional', 'EXACT_SHEET_123')
        self.assertNotEqual(result['status'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
