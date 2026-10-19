"""Independent graphical fields and source-bound movement never invent writing."""
from copy import deepcopy
import hashlib
import io
import json
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

from hub_v2 import completion, response_binding


SIZE = (1000, 750)


def unit(key, text, box, **extra):
    return {"key": key, "text": text, "box": box, "title": False, **extra}


def bind(student, teacher, *, panel_reference=None):
    written, _ = completion._written(student, teacher, SIZE)
    return completion._assign(completion.markers({"slides": [student]}), written, teacher, SIZE, student_slide=student, panel_reference=panel_reference)


def original_panels():
    teacher = {'number': 1, 'units': [
        unit('prompt', 'Explain the evidence in your own words.', (20, 10, 900, 25)),
        unit('first-label', 'Your first answer', (20, 60, 400, 25)),
        unit('first', '[[NOTES:C09:Q1]]', (20, 100, 400, 450)),
        unit('revised-label', 'Your revised answer', (550, 60, 400, 25)),
        unit('revised', '[[DRAFT:C09:Q1]]', (550, 100, 400, 450))]}
    student = deepcopy(teacher)
    for category, name in (('NOTES', 'first'), ('DRAFT', 'revised')):
        label = next(u for u in student['units'] if u['key'] == name + '-label')
        marker = {'category': category, 'code': 'C09', 'id': 'Q1'}
        label['binding'] = response_binding.panel_binding(marker, teacher, label, SIZE, completion._plain, completion.MARKER)
        label['text'] += '\n[[%s:C09:Q1]]' % category
        next(u for u in student['units'] if u['key'] == name)['text'] = ''
    student['units'].append(unit('added-answer', 'My independently written explanation uses the measured evidence.', (70, 400, 200, 50)))
    return teacher, student


def panel_authority(teacher):
    return response_binding.OriginalPanelReference({'slides': [teacher], 'size': SIZE},
        source_id='FictionalHandedOutReference', revision='frozen-7', provenance_sha256='a' * 64)


class OriginalPanelBindingTests(unittest.TestCase):
    def test_real_score_file_caller_has_no_original_reference_loader_and_stays_held(self):
        from hub_v2 import google
        from test_completion_readiness import single_assignment_agenda, work_result, FORM, STUDENT, TEMPLATE
        teacher, student = original_panels()
        header = {'number': 1, 'units': [unit('inventory', '[[MARKER-INVENTORY:C09]] '
            'NOTES=1 DRAFT=1 OPTIONAL=0 BANK_TERMS=0 NOTES_IDS=Q1 DRAFT_IDS=Q1 OPTIONAL_IDS=-', (0, 0, 900, 40))]}
        teacher['number'] = student['number'] = 2
        reference = {'slides': [deepcopy(header), teacher], 'size': SIZE}
        received = {'slides': [deepcopy(header), student], 'size': SIZE}
        completion._results.clear()
        self.addCleanup(completion._results.clear)
        with patch.object(completion, 'student_coding_required', return_value=False), \
             patch.object(completion, '_template', return_value=({'id': TEMPLATE, 'name': 'Fictional original',
                 'mimeType': google.PPTX, 'version': '7', 'modifiedTime': 'frozen-time'}, reference)), \
             patch.object(google, 'drive_file', return_value={'id': STUDENT, 'mimeType': google.PPTX,
                 'version': '8', 'modifiedTime': 'after-repair'}), \
             patch.object(completion, '_read', return_value=received), patch.object(completion, 'score') as score:
            result = completion.score_file('read', single_assignment_agenda(), 'forensics', 'Wk 04', FORM, STUDENT, work_result())
        self.assertEqual(result['code'], 'C09')
        self.assertIsNone(result['notes_score'])
        self.assertIsNone(result['revision_score'])
        self.assertIn('frozen original-source reference', result['note'])
        score.assert_not_called()

    def test_default_normal_assessment_and_description_only_spoof_are_held(self):
        teacher, student = original_panels()
        for authority in (None, {'verified': True, 'source': teacher}, student['units'][1]['binding']):
            with self.subTest(authority=type(authority).__name__), patch.object(completion, 'score') as score:
                result = completion.assess({'slides': [student], 'size': SIZE},
                    {'slides': [teacher], 'size': SIZE}, panel_reference=authority)
                self.assertIsNone(result['notes_score'])
                self.assertIsNone(result['revision_score'])
                self.assertTrue(result['note'])
                score.assert_not_called()

    def test_reference_capability_does_not_follow_mutated_or_newer_source(self):
        teacher, student = original_panels()
        authority = panel_authority(teacher)
        teacher['units'][0]['text'] = 'A newly updated question.'
        # Even reissuing internally consistent student metadata cannot update
        # the independently frozen source capability.
        for name, category in (('first', 'NOTES'), ('revised', 'DRAFT')):
            label = next(u for u in student['units'] if u['key'] == name + '-label')
            label['binding'] = response_binding.panel_binding({'category': category, 'code': 'C09', 'id': 'Q1'},
                teacher, label, SIZE, completion._plain, completion.MARKER)
        self.assertEqual(bind(student, teacher, panel_reference=authority)[2], response_binding.REVIEW)

    def test_added_answer_far_from_label_binds_only_inside_original_panel(self):
        teacher, student = original_panels()
        assigned, loose, basis = bind(student, teacher, panel_reference=panel_authority(teacher))
        self.assertEqual(basis, 'authenticated original response panels')
        self.assertEqual([u['key'] for u, _ in assigned[('NOTES', 'Q1')]], ['added-answer'])
        self.assertEqual(assigned[('DRAFT', 'Q1')], [])
        self.assertEqual(loose, [])
        result = completion.assess({'slides': [student], 'size': SIZE}, {'slides': [teacher], 'size': SIZE}, panel_reference=panel_authority(teacher))
        self.assertEqual(result['counts']['NOTES']['done'], 1)
        self.assertEqual(result['counts']['DRAFT']['done'], 0)

    def test_unbound_added_answer_does_not_gain_global_proximity_fallback(self):
        teacher, student = original_panels()
        for u in student['units']:
            u.pop('binding', None)
        assigned, loose, _ = bind(student, teacher, panel_reference=panel_authority(teacher))
        self.assertFalse(any(assigned.values()))
        self.assertEqual([u['key'] for u, _ in loose], ['added-answer'])

    def test_missing_changed_or_duplicate_reference_never_falls_back_to_position(self):
        teacher, student = original_panels()
        changed = deepcopy(teacher)
        changed['units'][0]['text'] = 'A newer different question must not authorize the original.'
        duplicate = deepcopy(teacher); duplicate['number'] = 2
        wrong_cycle = deepcopy(teacher)
        wrong_cycle['units'][2]['text'] = '[[NOTES:C10:Q1]]'
        for pages in ([], [changed], [teacher, duplicate], [wrong_cycle], [student]):
            with self.subTest(pages=len(pages)):
                pairs = completion._align({'slides': [student], 'size': SIZE}, {'slides': pages, 'size': SIZE}, panel_reference=panel_authority(teacher))
                self.assertNotIn(1, pairs)
                assigned, _, basis = bind(student, pairs.get(1), panel_reference=panel_authority(teacher))
                self.assertEqual(basis, response_binding.REVIEW)
                self.assertFalse(any(assigned.values()))

    def test_tampered_description_cannot_choose_another_panel_or_cycle(self):
        teacher, baseline = original_panels()
        for mutation in ('panel', 'cycle', 'extra', 'label', 'fingerprint'):
            student = deepcopy(baseline)
            data = student['units'][1]['binding']
            if mutation == 'panel': data['panels'][1]['box'][0] += 20
            elif mutation == 'cycle': data['cycle'] = 'C10'
            elif mutation == 'extra': data['authorized'] = True
            elif mutation == 'label': data['label']['text'] = 'Another label'
            else: data['source_sha256'] = '0' * 64
            with self.subTest(mutation=mutation):
                self.assertEqual(bind(student, teacher, panel_reference=panel_authority(teacher))[2], response_binding.REVIEW)

    def test_cross_panel_response_discards_even_earlier_valid_assignments(self):
        teacher, student = original_panels()
        student['units'].append(unit('crossing', 'A later response crosses both fields.', (400, 300, 200, 30)))
        assigned, loose, basis = bind(student, teacher, panel_reference=panel_authority(teacher))
        self.assertEqual(basis, response_binding.REVIEW)
        self.assertFalse(any(assigned.values()))
        self.assertEqual({u['key'] for u, _ in loose}, {'added-answer', 'crossing'})

    def test_moved_duplicate_missing_panel_and_changed_label_are_refused(self):
        teacher, baseline = original_panels()
        for change in ('moved', 'duplicate', 'missing', 'label'):
            student = deepcopy(baseline)
            if change == 'moved': student['units'][2]['box'] = (30, 100, 400, 450)
            elif change == 'duplicate': student['units'].append(deepcopy(student['units'][2]))
            elif change == 'missing': student['units'].pop(2)
            else: student['units'][1]['text'] = 'Changed label [[NOTES:C09:Q1]]'
            with self.subTest(change=change):
                self.assertEqual(bind(student, teacher, panel_reference=panel_authority(teacher))[2], response_binding.REVIEW)

    def test_overlapping_or_duplicate_original_panels_cannot_issue_binding(self):
        teacher, _ = original_panels()
        for change in ('overlap', 'duplicate', 'other-code'):
            ref = deepcopy(teacher)
            if change == 'overlap': ref['units'][4]['box'] = (400, 100, 400, 450)
            elif change == 'duplicate': ref['units'].append(deepcopy(ref['units'][2]))
            else: ref['units'].append(unit('another', '[[NOTES:C09:OTHER]]', (20, 600, 200, 30)))
            with self.subTest(change=change):
                self.assertIsNone(response_binding.panel_binding({'category': 'NOTES', 'code': 'C09', 'id': 'Q1'},
                    ref, teacher['units'][1], SIZE, completion._plain, completion.MARKER))

    def test_blank_panels_and_outside_text_remain_blank_or_unassigned(self):
        teacher, student = original_panels()
        student['units'].pop()
        student['units'].append(unit('unrelated', 'Some writing outside either original response panel.', (20, 650, 500, 30)))
        assigned, loose, basis = bind(student, teacher, panel_reference=panel_authority(teacher))
        self.assertEqual(basis, 'authenticated original response panels')
        self.assertFalse(any(assigned.values()))
        self.assertEqual([u['key'] for u, _ in loose], ['unrelated'])


def fields():
    # Graphical underlines are not teacher text boxes. Added student labels
    # should bind by location rather than the number of earlier filled fields.
    return {"number": 1, "units": [unit("heading", "Write each organism in its own diagram field.", (20, 20, 900, 40))] + [
        unit(f"code-{n}", f"[[NOTES:C09:SLOT-{n}]]", (100 + (n % 3) * 220, 150 + (n // 3) * 150, 2, 1))
        for n in range(9)]}


def stock_picture(key, content, box):
    return unit(key, "", box, picture={"sha256": hashlib.sha256(content.encode()).hexdigest(), "appearance": {}})


def picture_diagram():
    teacher = fields()
    pictures = []
    for n in range(9):
        x, y = 80 + (n % 3) * 220, 80 + (n // 3) * 150
        teacher["units"].append(stock_picture(f"outline-{n}", "outline" + str(n % 2), (x, y, 80, 60)))
    for n in range(8):
        p = stock_picture(f"organism-{n}", "organism" + str(n), (800, 40 + n * 80, 60, 60))
        teacher["units"].append(p)
        pictures.append({"sha256": p["picture"]["sha256"], "box": p["box"]})
    student = deepcopy(teacher)
    for n in range(9):
        target = next(u for u in teacher["units"] if u["key"] == f"outline-{n}")
        code = next(u for u in student["units"] if u["key"] == f"code-{n}")
        code["binding"] = {"kind": "picture-slot", "box": target["box"], "source_box": target["box"],
            "target_picture_sha256": target["picture"]["sha256"], "pictures": deepcopy(pictures)}
    return teacher, student


def station():
    teacher = {"number": 1, "units": [unit("instructions", "Move each organism card into one feeding role.", (20, 20, 900, 50)),
        unit("a-role", "Producer", (20, 150, 350, 250)), unit("b-role", "Consumer", (420, 150, 350, 250)),
        unit("grass", "Grass", (20, 500, 150, 50)), unit("rabbit", "Rabbit", (220, 500, 150, 50))]}
    student = deepcopy(teacher)
    student["units"].append(unit("code", "[[NOTES:C09:STATION]]", (30, 30, 1, 1), binding={
        "kind": "drag-sort", "cards": [{"text": "Grass", "box": (20, 500, 150, 50)}, {"text": "Rabbit", "box": (220, 500, 150, 50)}],
        "targets": [{"label": "Producer", "source_box": (20, 150, 350, 250), "box": (20, 150, 350, 250)},
                    {"label": "Consumer", "source_box": (420, 150, 350, 250), "box": (420, 150, 350, 250)}]}))
    return teacher, student


def picture_activity():
    teacher, student = picture_diagram()
    targets = []
    pictures = next(u for u in student["units"] if u["key"] == "code-0")["binding"]["pictures"]
    for n in range(9):
        silhouette = next(u for u in teacher["units"] if u["key"] == f"outline-{n}")
        x, y, w, h = silhouette["box"]
        line = stock_picture(f"line-{n}", "original underline", (x, y + h + 4, w, 2))
        teacher["units"].append(line); student["units"].append(deepcopy(line))
        targets.append({"box": silhouette["box"], "source_box": silhouette["box"],
            "target_picture_sha256": silhouette["picture"]["sha256"], "response_box": line["box"],
            "source_response_box": line["box"], "response_picture_sha256": line["picture"]["sha256"]})
    for slide in (teacher, student):
        slide["units"] = [u for u in slide["units"] if not u["key"].startswith("code-")]
        slide["units"].append(unit("activity", "[[NOTES:C09:FOODWEB-ACTIVITY]]", (30, 30, 1, 1)))
    student["units"][-1]["binding"] = {"kind": "picture-activity", "targets": targets, "pictures": pictures}
    return teacher, student


class PointBindingTests(unittest.TestCase):
    def test_blank_additive_codes_create_no_answers(self):
        teacher = fields()
        assigned, loose, _ = bind(deepcopy(teacher), teacher)
        self.assertFalse(any(assigned.values()))
        self.assertEqual(loose, [])

    def test_all_nine_slots_and_arbitrary_partial_fill_are_independent(self):
        teacher = fields()
        for filled in ((8,), (1, 4, 8), tuple(range(9))):
            with self.subTest(filled=filled):
                student = deepcopy(teacher)
                for n in filled:
                    x, y, _, _ = student["units"][n + 1]["box"]
                    student["units"].append(unit(f"answer-{n}", "An independent organism label", (x - 20, y - 1, 60, 4)))
                assigned, loose, basis = bind(student, teacher)
                self.assertEqual({key for key, values in assigned.items() if values}, {("NOTES", f"SLOT-{n}") for n in filled})
                self.assertEqual(loose, [])
                self.assertEqual(basis, "independent response points")

    def test_changed_printed_prompt_is_not_an_answer_to_a_separate_actual_box(self):
        teacher = {"number": 1, "units": [unit("prompt", "A different current printed question.", (20, 20, 500, 50)),
            unit("answer", "", (20, 200, 500, 300))]}
        student = deepcopy(teacher)
        student["units"][0]["text"] = "The older printed question actually received."
        student["units"].append(unit("code", "[[NOTES:C09:RECEIVED]]", (250, 350, 1, 1)))
        assigned, loose, _ = bind(student, teacher)
        self.assertEqual(assigned[("NOTES", "RECEIVED")], [])
        self.assertEqual([u["key"] for u, _ in loose], ["prompt"])
        student["units"][1]["text"] = "An independent response to my received question."
        assigned, _, _ = bind(student, teacher)
        self.assertEqual([u["key"] for u, _ in assigned[("NOTES", "RECEIVED")]], ["answer"])

    def test_one_large_response_with_many_points_does_not_guess(self):
        teacher = fields()
        student = deepcopy(teacher)
        student["units"].append(unit("ambiguous", "A response spanning several fields", (50, 100, 800, 400)))
        assigned, loose, _ = bind(student, teacher)
        self.assertFalse(any(assigned.values()))
        self.assertEqual([u["key"] for u, _ in loose], ["ambiguous"])
        self.assertEqual(bind(student, teacher)[2], response_binding.REVIEW)

    def test_older_received_printed_question_copied_into_answer_is_not_credited(self):
        teacher = {"number": 1, "units": [unit("prompt", "A different current printed biology question.", (20, 20, 500, 50)),
            unit("answer", "", (20, 200, 500, 300)),
            unit("code", "[[NOTES:C09:RECEIVED]]", (250, 350, 1, 1))]}
        student = deepcopy(teacher)
        received = "Explain how organisms get energy from the food they eat."
        student["units"][0]["text"] = received
        student["units"][1]["text"] = received
        result = completion.assess({"slides": [student], "size": SIZE}, {"slides": [teacher], "size": SIZE})
        self.assertEqual(result["items"][0]["status"], "copied")
        self.assertEqual(result["counts"]["NOTES"]["done"], 0)
        student["units"][1]["text"] = "I explain my understanding in my own independently written response."
        result = completion.assess({"slides": [student], "size": SIZE}, {"slides": [teacher], "size": SIZE})
        self.assertEqual(result["items"][0]["status"], "done")

    def test_export_renamed_answer_colliding_with_prompt_name_keeps_unique_blank_geometry(self):
        teacher = {"number": 1, "units": [unit("prompt", "The original printed question.", (20, 20, 500, 50)),
            unit("answer", "", (20, 200, 500, 300)),
            unit("code", "[[NOTES:C09:RECEIVED]]", (250, 350, 1, 1))]}
        student = deepcopy(teacher)
        student["units"][0]["key"] = "renamed-prompt"
        student["units"][1]["key"] = "prompt"
        student["units"][1]["text"] = "An independently written answer in the authentic blank response field."
        result = completion.assess({"slides": [student], "size": SIZE}, {"slides": [teacher], "size": SIZE})
        self.assertEqual(result["items"][0]["status"], "done")

    def test_export_renamed_retained_prompt_geometry_still_detects_copied_old_question(self):
        teacher = {"number": 1, "units": [unit("prompt", "The current different printed question.", (20, 20, 500, 50)),
            unit("answer", "", (20, 200, 500, 300)),
            unit("code", "[[NOTES:C09:RECEIVED]]", (250, 350, 1, 1))]}
        student = deepcopy(teacher)
        actual_printed = "Explain how organisms get energy from the food they eat."
        student["units"][0]["key"] = "answer"
        student["units"][0]["text"] = actual_printed
        student["units"][1]["key"] = "renamed-response"
        student["units"][1]["text"] = actual_printed
        result = completion.assess({"slides": [student], "size": SIZE}, {"slides": [teacher], "size": SIZE})
        self.assertEqual(result["items"][0]["status"], "copied")
        self.assertEqual(result["counts"]["NOTES"]["done"], 0)

    def test_two_actual_response_boxes_claiming_one_point_are_held(self):
        teacher = fields(); student = deepcopy(teacher)
        for n in range(2):
            student["units"].append(unit(f"answer-{n}", "A competing written label", (80, 149, 60, 4)))
        self.assertEqual(bind(student, teacher)[2], response_binding.REVIEW)

    def test_large_added_revision_overlay_is_bound_inside_authentic_blank_region(self):
        teacher = {"number": 1, "units": [unit("label", "Your revised answer", (420, 50, 350, 40)),
            unit("original-blank", "", (420, 100, 350, 350))]}
        student = deepcopy(teacher)
        student["units"].append(unit("code", "[[DRAFT:C09:Q]]", (590, 275, 1, 1)))
        student["units"].append(unit("overlay", "An independent revised explanation", (419.5, 200, 350.4, 160)))
        assigned, _, _ = bind(student, teacher)
        self.assertEqual([u["key"] for u, _ in assigned[("DRAFT", "Q")]], ["overlay"])
        student["units"][-1]["box"] = (200, 200, 600, 160)
        self.assertFalse(any(bind(student, teacher)[0].values()))

    def test_shifted_received_response_remains_grounded_in_authentic_coded_field(self):
        teacher = {"number": 1, "units": [unit("prompt", "The retained printed question", (20, 20, 700, 50)),
            unit("original-slot", "Your response here\n[[NOTES:C09:Q]]", (50, 350, 700, 130))]}
        student = deepcopy(teacher)
        student["units"][1]["text"] = "An independently written response."
        student["units"][1]["box"] = (65, 300, 720, 140)
        student["units"].append(unit("code", "[[NOTES:C09:Q]]", (420, 380, 1, 1)))
        assigned, _, _ = bind(student, teacher)
        self.assertEqual([u["key"] for u, _ in assigned[("NOTES", "Q")]], ["original-slot"])

    def test_shared_large_offcanvas_and_bank_code_behavior_is_not_extended(self):
        for text, box in (("[[NOTES:C09:A]]\n[[NOTES:C09:B]]", (100, 100, 1, 1)),
                          ("[[NOTES:C09:A]]", (100, 100, 200, 100)),
                          ("[[NOTES:C09:A]]", (100, 800, 1, 1)),
                          ("[[BANK:C09:AmbiguousTerm]]", (100, 100, 1, 1))):
            with self.subTest(text=text, box=box):
                teacher = {"number": 1, "units": [unit("codes", text, box)]}
                self.assertFalse(response_binding.tiny_points(completion.markers({"slides": [teacher]}), SIZE))
        teacher = {"number": 1, "units": [unit("heading", "Term group", (20, 20, 500, 30)),
            unit("answer", "", (100, 150, 300, 50)), unit("codes", "[[BANK:C09:AmbiguousTerm]]", (50, 50, 1, 1))]}
        student = deepcopy(teacher)
        student["units"][1]["text"] = "A definition without a unique term label."
        assigned, _, basis = bind(student, teacher)
        self.assertFalse(any(assigned.values()))
        self.assertEqual(basis, "BANK term labels need review")


class DragBindingTests(unittest.TestCase):
    def test_blank_tray_and_added_code_are_not_student_answers(self):
        teacher, student = station()
        self.assertFalse(any(bind(student, teacher)[0].values()))

    def test_only_an_unchanged_original_card_moved_into_a_role_receives_activity_credit(self):
        teacher, student = station()
        next(u for u in student["units"] if u["key"] == "grass")["box"] = (60, 220, 150, 50)
        assigned, _, basis = bind(student, teacher)
        self.assertEqual([u["key"] for u, _ in assigned[("NOTES", "STATION")]], ["grass"])
        self.assertEqual(basis, "original card placement")
        reference = deepcopy(teacher)
        reference["units"].append(unit("teacher-code", "[[NOTES:C09:STATION]]", (30, 30, 1, 1)))
        result = completion.assess({"slides": [student], "size": SIZE}, {"slides": [reference], "size": SIZE})
        self.assertEqual(result["items"][0]["answer"], "")
        self.assertIn("card", result["items"][0]["completion_evidence"].lower())

    def test_moved_heading_changed_prompt_and_retyped_card_do_not_receive_credit(self):
        teacher, student = station()
        next(u for u in student["units"] if u["key"] == "instructions")["box"] = (60, 220, 150, 50)
        student["units"].append(unit("typed", "Rabbit", (500, 200, 150, 50)))
        self.assertFalse(any(bind(student, teacher)[0].values()))
        student["units"][0]["text"] = "A changed printed direction."
        self.assertFalse(any(bind(student, teacher)[0].values()))

    def test_duplicate_card_or_unverified_target_holds(self):
        for duplicate in (True, False):
            teacher, student = station()
            if duplicate:
                student["units"].append(unit("copy", "Grass", (50, 200, 150, 50)))
            else:
                student["units"][-1]["binding"]["targets"][0]["source_box"] = (25, 150, 350, 250)
            self.assertEqual(bind(student, teacher)[2], response_binding.REVIEW)

    def test_verified_resized_received_target_still_uses_authentic_source_card(self):
        teacher, student = station()
        next(u for u in student["units"] if u["key"] == "a-role")["box"] = (20, 150, 350, 450)
        student["units"][-1]["binding"]["targets"][0]["box"] = (20, 150, 350, 450)
        next(u for u in student["units"] if u["key"] == "grass")["box"] = (60, 200, 145, 48)
        assigned, _, _ = bind(student, teacher)
        self.assertEqual([u["key"] for u, _ in assigned[("NOTES", "STATION")]], ["grass"])

    def test_single_station_attempt_survives_known_offtarget_stacked_original_card(self):
        teacher, student = station()
        next(u for u in student["units"] if u["key"] == "grass")["box"] = (60, 220, 150, 50)
        next(u for u in student["units"] if u["key"] == "rabbit")["box"] = (420, 450, 150, 50)
        assigned, loose, basis = bind(student, teacher)
        self.assertEqual([u["key"] for u, _ in assigned[("NOTES", "STATION")]], ["grass"])
        self.assertEqual([(u["key"], words) for u, words in loose], [("rabbit", "Off-target original card placement")])
        self.assertEqual(basis, "original card placement")
        next(u for u in student["units"] if u["key"] == "grass")["box"] = (20, 500, 150, 50)
        self.assertEqual(bind(student, teacher)[2], response_binding.REVIEW)

    def test_station_card_in_multiple_authentic_roles_is_always_held(self):
        teacher, student = station()
        next(u for u in student["units"] if u["key"] == "b-role")["box"] = (200, 150, 350, 250)
        student["units"][-1]["binding"]["targets"][1]["box"] = (200, 150, 350, 250)
        next(u for u in student["units"] if u["key"] == "grass")["box"] = (250, 220, 150, 50)
        self.assertEqual(bind(student, teacher)[2], response_binding.REVIEW)


class PictureBindingTests(unittest.TestCase):
    def test_description_newline_and_xml_normalized_space_are_parsed_but_bad_known_header_holds(self):
        data = {"kind": "picture-activity", "targets": [], "pictures": []}
        for delimiter in ("\n", " ", "\r\n"):
            self.assertEqual(response_binding.description(response_binding.HEADER + delimiter + json.dumps(data)), data)
        for suffix in ("not JSON", " []", " {bad}", "", " " + "x" * 40_000):
            self.assertEqual(response_binding.description(response_binding.HEADER + suffix), {"invalid": True})
        self.assertIsNone(response_binding.description("Unrelated image alt text"))
    def test_valid_activity_attempt_is_not_cancelled_by_another_known_offtarget_card(self):
        teacher, student = picture_activity()
        next(u for u in student["units"] if u["key"] == "organism-0")["box"] = (90, 85, 60, 60)
        next(u for u in student["units"] if u["key"] == "organism-1")["box"] = (700, 700, 60, 40)
        result = completion.assess({"slides": [student], "size": SIZE}, {"slides": [teacher], "size": SIZE})
        self.assertEqual(result["notes_score"], 10.0)
        self.assertTrue(any("Off-target" in r["excerpt"] for r in result["uncoded"]))

    def test_unmoved_stock_crop_does_not_cancel_another_genuine_placement(self):
        teacher, student = picture_activity()
        next(u for u in student["units"] if u["key"] == "organism-0")["box"] = (90, 85, 60, 60)
        next(u for u in student["units"] if u["key"] == "organism-1")["picture"]["appearance"] = {"crop": {"l": "10000"}}
        result = completion.assess({"slides": [student], "size": SIZE}, {"slides": [teacher], "size": SIZE})
        self.assertEqual(result["notes_score"], 10.0)
        self.assertEqual(result["counts"]["NOTES"]["done"], 1)

    def test_known_original_moved_crop_is_quality_only_and_needs_an_independent_valid_attempt(self):
        teacher, student = picture_activity()
        cropped = next(u for u in student["units"] if u["key"] == "organism-1")
        cropped["box"] = (310, 85, 60, 60)
        cropped["picture"]["appearance"] = {"crop": {"l": "10000"}}
        self.assertEqual(bind(student, teacher)[2], response_binding.REVIEW)
        next(u for u in student["units"] if u["key"] == "organism-0")["box"] = (90, 85, 60, 60)
        assigned, loose, basis = bind(student, teacher)
        self.assertEqual([u["key"] for u, _ in assigned[("NOTES", "FOODWEB-ACTIVITY")]], ["organism-0"])
        self.assertEqual([words for _, words in loose], ["Changed-crop original picture placement"])
        self.assertEqual(basis, "original picture placement")
        cropped["picture"]["appearance"]["groups"] = [{"flipH": "1"}]
        self.assertEqual(bind(student, teacher)[2], response_binding.REVIEW)

    def test_only_offtarget_movement_or_unmatched_explicit_baseline_holds(self):
        teacher, student = picture_activity()
        next(u for u in student["units"] if u["key"] == "organism-0")["box"] = (700, 700, 60, 40)
        self.assertEqual(bind(student, teacher)[2], response_binding.REVIEW)
        with patch.object(completion, "_align", return_value={}), patch.object(completion, "score", side_effect=AssertionError("Missing baseline must hold")):
            result = completion.assess({"slides": [student], "size": SIZE}, {"slides": [teacher], "size": SIZE})
        self.assertEqual((result["notes_score"], result["revision_score"]), (None, None))
    def test_eight_cards_and_nine_targets_are_one_actual_activity_not_nine_required_organisms(self):
        teacher, student = picture_activity()
        for n in range(8):
            target = next(u for u in teacher["units"] if u["key"] == f"outline-{n}")
            x, y, _, _ = target["box"]
            next(u for u in student["units"] if u["key"] == f"organism-{n}")["box"] = (x + 10, y + 5, 60, 60)
        result = completion.assess({"slides": [student], "size": SIZE}, {"slides": [teacher], "size": SIZE})
        self.assertEqual(result["counts"]["NOTES"], {"done": 1, "required": 1})
        self.assertEqual(result["notes_score"], 10.0)
        self.assertEqual(result["items"][0]["answer"], "")

    def test_new_single_activity_id_aligns_only_unique_authentic_media_manifest(self):
        teacher, student = picture_activity()
        teacher["units"][-1]["text"] = "[[NOTES:C09:OLD-SLOT-ID]]"
        next(u for u in student["units"] if u["key"] == "organism-0")["box"] = (90, 85, 60, 60)
        inventory = unit("inventory", "[[MARKER-INVENTORY:C09]] NOTES=1 DRAFT=0 OPTIONAL=0 BANK_TERMS=0 NOTES_IDS=FOODWEB-ACTIVITY", (0, 0, 1, 1))
        student["units"].append(inventory)
        # Different lengths and changed ID prevent an incidental positional match.
        student["number"] = 2
        d={"slides": [{"number":1,"units":[]},student], "size": SIZE}
        t={"slides": [teacher], "size": SIZE}
        self.assertEqual(completion._align(d,t)[2]["number"],1)
        result=completion.assess(d,t)
        self.assertEqual(result["items"][0]["status"],"done")
        duplicate=deepcopy(teacher);duplicate["number"]=2
        result=completion.assess(d,{"slides":[teacher,duplicate],"size":SIZE})
        self.assertIsNone(result["notes_score"])
        self.assertIn("original cards or pictures",result["note"])

    def test_actual_name_line_writing_counts_but_blank_received_copy_and_changed_prompt_do_not(self):
        teacher, student = picture_activity()
        self.assertFalse(any(bind(student, teacher)[0].values()))
        student["units"][0]["text"] = "An older independently retained printed diagram instruction."
        self.assertFalse(any(bind(student, teacher)[0].values()))
        x, y, w, h = student["units"][-1]["binding"]["targets"][5]["response_box"]
        student["units"].append(unit("actual-name", "An independently written organism name", (x, y, w, h)))
        assigned, _, _ = bind(student, teacher)
        self.assertEqual([u["key"] for u, _ in assigned[("NOTES", "FOODWEB-ACTIVITY")]], ["actual-name"])

    def test_actual_name_on_silhouette_or_both_locations_is_one_authentic_response(self):
        for both in (False, True):
            teacher, student = picture_activity()
            x, y, w, h = student["units"][-1]["binding"]["targets"][0]["box"]
            box = (x, y, w, h + 10 if both else h)
            teacher["units"].append(unit("actual-name", "", box))
            student["units"].append(unit("actual-name", "An actual organism name", box))
            assigned, _, basis = bind(student, teacher)
            self.assertEqual([u["key"] for u, _ in assigned[("NOTES", "FOODWEB-ACTIVITY")]], ["actual-name"])
            self.assertEqual(basis, "original picture placement")

    def test_added_name_overlay_on_authenticated_graphical_activity_is_genuine_writing(self):
        teacher, student = picture_activity()
        student["units"].append(unit("actual-name", "A genuine organism name", (80, 85, 600, 55)))
        assigned, _, _ = bind(student, teacher)
        self.assertEqual([u["key"] for u, _ in assigned[("NOTES", "FOODWEB-ACTIVITY")]], ["actual-name"])
        student["units"][-1]["box"] = (80, 85, 600, 300)
        self.assertFalse(any(bind(student, teacher)[0].values()))

    def test_malformed_explicit_binding_holds_instead_of_using_generic_fallback(self):
        teacher, student = picture_activity()
        student["units"][-1]["binding"] = {"invalid": True}
        with patch.object(completion, "score", side_effect=AssertionError("Invalid metadata must not score")):
            result = completion.assess({"slides": [student], "size": SIZE}, {"slides": [teacher], "size": SIZE})
        self.assertEqual((result["notes_score"], result["revision_score"]), (None, None))

    def test_unique_original_picture_move_is_completion_evidence_without_a_written_quote(self):
        teacher, student = picture_diagram()
        next(u for u in student["units"] if u["key"] == "organism-0")["box"] = (90, 85, 60, 60)
        result = completion.assess({"slides": [student], "size": SIZE}, {"slides": [teacher], "size": SIZE})
        item = next(i for i in result["items"] if i["id"] == "SLOT-0")
        self.assertEqual(item["status"], "done")
        self.assertEqual(item["answer"], "")
        self.assertIn("picture", item["completion_evidence"].lower())
        self.assertEqual(result["counts"]["NOTES"]["done"], 1)

    def test_blank_original_pictures_are_not_answers(self):
        teacher, student = picture_diagram()
        assigned, _, _ = bind(student, teacher)
        self.assertFalse(any(assigned.values()))

    def test_unknown_decorative_ambiguous_or_offtarget_picture_is_held(self):
        for kind in ("unknown", "decorative", "duplicate", "offtarget", "changed-crop", "changed-group"):
            with self.subTest(kind=kind):
                teacher, student = picture_diagram()
                card = next(u for u in student["units"] if u["key"] == "organism-0")
                if kind == "offtarget":
                    card["box"] = (700, 700, 60, 40)
                elif kind == "duplicate":
                    duplicate = deepcopy(card); duplicate["key"] = "duplicate"; duplicate["box"] = (90, 85, 60, 60)
                    student["units"].append(duplicate)
                elif kind == "unknown":
                    student["units"].append(stock_picture("unknown", "not authentic", (90, 85, 60, 60)))
                elif kind == "decorative":
                    decoration = stock_picture("decoration", "decorative", (700, 700, 60, 40))
                    teacher["units"].append(deepcopy(decoration)); decoration["box"] = (90, 85, 60, 60)
                    student["units"].append(decoration)
                else:
                    card["box"] = (90, 85, 60, 60)
                    card["picture"]["appearance"] = ({"crop": {"l": "10000"}} if kind == "changed-crop"
                                                      else {"groups": [{"flipH": "1"}]})
                with patch.object(completion, "score", side_effect=AssertionError("Held binding must not produce a zero")):
                    result = completion.assess({"slides": [student], "size": SIZE}, {"slides": [teacher], "size": SIZE})
                self.assertEqual((result["notes_score"], result["revision_score"]), (None, None))
                self.assertIn("original cards or pictures", result["note"])

    def test_whole_registered_target_translation_retains_real_picture_binding(self):
        teacher, student = picture_diagram()
        for u in student["units"]:
            if u["key"].startswith("outline-"):
                x, y, w, h = u["box"]; u["box"] = (x - 20, y + 10, w, h)
            elif u["key"].startswith("code-"):
                x, y, w, h = u["binding"]["box"]; u["binding"]["box"] = (x - 20, y + 10, w, h)
        next(u for u in student["units"] if u["key"] == "organism-0")["box"] = (70, 95, 60, 60)
        self.assertEqual([u["key"] for u, _ in bind(student, teacher)[0][("NOTES", "SLOT-0")]], ["organism-0"])

    def test_parser_reads_embedded_media_hash_geometry_and_code_description(self):
        raw = io.BytesIO()
        binding = response_binding.PREFIX + json.dumps({"kind": "drag-sort", "cards": [], "targets": []})
        import html
        xml = f'''<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:cSld><p:spTree><p:pic><p:nvPicPr><p:cNvPr id="1" name="authentic"/></p:nvPicPr><p:blipFill><a:blip r:embed="rImage"/></p:blipFill><p:spPr><a:xfrm><a:off x="10" y="20"/><a:ext cx="30" cy="40"/></a:xfrm></p:spPr></p:pic><p:sp><p:nvSpPr><p:cNvPr id="2" name="code" descr="{html.escape(binding, quote=True).replace(chr(10), '&#10;')}"/></p:nvSpPr><p:spPr><a:xfrm><a:off x="10" y="20"/><a:ext cx="1" cy="1"/></a:xfrm></p:spPr><p:txBody><a:p><a:r><a:t>[[NOTES:C09:Q]]</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>'''
        media = b"synthetic original image bytes"
        with zipfile.ZipFile(raw, "w") as z:
            z.writestr("ppt/slides/slide1.xml", xml)
            z.writestr("ppt/slides/_rels/slide1.xml.rels", '<Relationships><Relationship Id="rImage" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/image1.png"/></Relationships>')
            z.writestr("ppt/media/image1.png", media)
        deck = completion.read_deck(raw.getvalue())
        image, code = deck["slides"][0]["units"]
        self.assertEqual(image["picture"]["sha256"], hashlib.sha256(media).hexdigest())
        self.assertEqual(image["box"], (10, 20, 30, 40))
        self.assertEqual(code["binding"]["kind"], "drag-sort")
        self.assertEqual(image["text"], "")

    def test_parser_retains_parent_picture_rotation_and_flip_for_appearance_guard(self):
        tree = ET.fromstring('''<p:spTree xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:grpSp><p:nvGrpSpPr><p:cNvPr id="1" name="group"/></p:nvGrpSpPr><p:grpSpPr><a:xfrm rot="60000" flipH="1"/></p:grpSpPr><p:pic><p:nvPicPr><p:cNvPr id="2" name="organism"/></p:nvPicPr><p:blipFill><a:blip r:embed="rImage"/></p:blipFill><p:spPr><a:xfrm><a:off x="10" y="20"/><a:ext cx="30" cy="40"/></a:xfrm></p:spPr></p:pic></p:grpSp></p:spTree>''')
        units = []
        completion._walk(tree, units, (1, 1, 0, 0), pictures={"rImage": "a" * 64})
        self.assertEqual(units[0]["picture"]["appearance"]["groups"], [{"rot": "60000", "flipH": "1"}])


if __name__ == "__main__":
    unittest.main()
