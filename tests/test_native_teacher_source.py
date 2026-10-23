"""Teacher fallback is exact, read-only and never a submitted-copy reader."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from hub_v2 import completion, google, native_teacher_source, response_binding
from test_completion_readiness import HEADER, source, work_result, TEMPLATE, STUDENT, FORM
from test_hub_v2 import pptx
from test_completion_readiness import single_assignment_agenda as fixture


def shape(key, text="", *, x=0, y=0, width=900, height=40):
    return {"objectId": key,
            "size": {"width": {"magnitude": width, "unit": "PT"},
                     "height": {"magnitude": height, "unit": "PT"}},
            "transform": {"scaleX": 1, "scaleY": 1, "translateX": x, "translateY": y, "unit": "PT"},
            "shape": {"shapeType": "TEXT_BOX", "shapeProperties": {"outline": {"propertyState": "NOT_RENDERED"}},
                      "text": {"textElements": [{"textRun": {"content": text, "style": {"bold": True}}}]}}}


def native():
    return {"presentationId": TEMPLATE,
            "pageSize": {"width": {"magnitude": 900, "unit": "PT"},
                         "height": {"magnitude": 500, "unit": "PT"}},
            "masters": [{"objectId": "master", "pageProperties": {"colorScheme": {"colors": []}}}],
            "slides": [{"objectId": "slide1", "pageElements": [shape("inventory", HEADER)]},
                       {"objectId": "slide2", "pageElements": [
                           shape("question", "Explain the observation."),
                           shape("answer", y=100, height=100),
                           shape("notes", "[[NOTES:F06:Q-MASTERY]]", y=80, width=10, height=10),
                           shape("revised", "Your revised answer", y=240, height=30),
                           shape("revision", y=280, height=100),
                           shape("draft", "[[DRAFT:F06:Q-MASTERY]]", y=260, width=10, height=10)]}]}


def metadata(version="1", **changes):
    return {"id": TEMPLATE, "name": "Fictional source", "mimeType": completion.SLIDES,
            "trashed": False, "modifiedTime": "unchanged", "version": version, **changes}


def empty_decoration():
    value = shape("empty-decoration")
    value["transform"].pop("scaleX")
    value["shape"] = {"shapeType": "CUSTOM", "shapeProperties": {
        "shapeBackgroundFill": {"solidFill": {"color": {"rgbColor": {"red": 1}}, "alpha": 1}},
        "outline": {"weight": {"magnitude": 1, "unit": "PT"}, "dashStyle": "SOLID"},
        "shadow": {"propertyState": "NOT_RENDERED"}, "contentAlignment": "MIDDLE",
        "autofit": {"autofitType": "NONE", "fontScale": 1}}}
    return value


class NativeModelTests(unittest.TestCase):
    def read(self, value=None, **limits):
        return native_teacher_source.read(value if value is not None else native(),
                                         presentation_id=TEMPLATE, revision="1", **limits)

    def test_exact_text_styles_master_geometry_and_revision_are_retained_without_aliasing(self):
        original = native()
        original["slides"][1]["pageElements"][0]["shape"]["text"]["textElements"].append(
            {"textRun": {"content": " Next line\n", "style": {"italic": True}}})
        result = self.read(original)
        question = result["slides"][1]["units"][0]
        self.assertEqual(question["text"], "Explain the observation. Next line")
        self.assertEqual(question["box"], (0, 0, 900 * 12700, 40 * 12700))
        self.assertEqual(result["size"], (900 * 12700, 500 * 12700))
        self.assertEqual(result["native_source"], original)
        self.assertEqual(question["native_style"], original["slides"][1]["pageElements"][0]["shape"])
        self.assertEqual((result["native_source_id"], result["native_source_revision"]), (TEMPLATE, "1"))
        original["masters"][0]["objectId"] = "changed"
        original["slides"][1]["pageElements"][0]["shape"]["text"]["textElements"][0]["textRun"]["style"]["bold"] = False
        self.assertEqual(result["native_source"]["masters"][0]["objectId"], "master")
        self.assertTrue(question["native_style"]["text"]["textElements"][0]["textRun"]["style"]["bold"])

    def test_group_affine_transform_and_emu_dimensions_compose_in_parent_order(self):
        value = native()
        child = shape("nested", "Text", x=10, y=20, width=5, height=7)
        value["slides"][0]["pageElements"] = [{"objectId": "group", "transform": {
            "scaleX": 2, "scaleY": 3, "translateX": 100, "translateY": 200, "unit": "PT"},
            "elementGroup": {"children": [child]}}]
        box = self.read(value)["slides"][0]["units"][0]["box"]
        self.assertEqual(box, (120 * 12700, 260 * 12700, 10 * 12700, 21 * 12700))
        value["pageSize"] = {"width": {"magnitude": 900 * 12700, "unit": "EMU"},
                             "height": {"magnitude": 500 * 12700, "unit": "EMU"}}
        self.assertEqual(self.read(value)["size"], (900 * 12700, 500 * 12700))

    def test_image_keeps_native_properties_without_download_or_fictitious_fingerprint(self):
        value = native()
        image = shape("photo")
        image.pop("shape")
        image["image"] = {"contentUrl": "https://example.invalid/private-image", "imageProperties": {"cropProperties": {"leftOffset": .1}}}
        value["slides"][0]["pageElements"] = [image]
        unit = self.read(value)["slides"][0]["units"][0]
        self.assertIsNone(unit["picture"]["sha256"])
        self.assertEqual(unit["native_style"], image["image"])

    def test_zero_width_empty_custom_decoration_does_not_hide_other_evidence(self):
        original = native()
        expected = self.read(original)
        original["slides"][1]["pageElements"].append(empty_decoration())
        snapshot = deepcopy(original)
        result = self.read(original)
        self.assertEqual(result["slides"], expected["slides"])
        self.assertEqual(result["native_source"], snapshot)
        self.assertEqual(original, snapshot)
        self.assertEqual(completion.teacher_readiness(result, require_complete_codes=False),
                         completion.teacher_readiness(expected, require_complete_codes=False))
        # A drawable empty shape is retained, so this is not a blanket removal
        # of shapes without text or a substitute for response-region checks.
        original["slides"][1]["pageElements"][-1]["transform"]["scaleX"] = 1
        self.assertEqual(self.read(original)["slides"][1]["units"][-1]["key"], "empty-decoration")

    def test_collapsed_nonempty_or_ambiguous_nodes_still_hold(self):
        mutations = [lambda e: e["shape"].update(text={"textElements": []}),
                     lambda e: e["shape"].update(text={"textElements": [{"textRun": {"content": "Answer here"}}]}),
                     lambda e: e["shape"].update(text={"textElements": [{"textRun": {"content": "[[NOTES:X:Q]]"}}]}),
                     lambda e: e["shape"].update(placeholder={"type": "BODY"}),
                     lambda e: e["shape"].update(shapeType="TEXT_BOX"),
                     lambda e: e.update(description="Response evidence"),
                     lambda e: e.update(title="Describe the scene"),
                     lambda e: e.update(image={}), lambda e: e.update(table={}),
                     lambda e: e.update(futureContent={}),
                     lambda e: e["shape"].update(futureContent={}),
                     lambda e: e["shape"]["shapeProperties"].update(link={"url": "https://example.invalid"}),
                     lambda e: e["shape"]["shapeProperties"]["shapeBackgroundFill"].update(image={}),
                     lambda e: e["shape"]["shapeProperties"]["outline"].update(futureContent={}),
                     lambda e: e["shape"]["shapeProperties"].update(autofit=[])]
        for mutation in mutations:
            value = native(); node = empty_decoration(); mutation(node)
            value["slides"][1]["pageElements"].append(node)
            with self.subTest(mutation=mutation), self.assertRaises(native_teacher_source.Unreadable):
                self.read(value)

    def test_grouped_empty_leaf_retains_every_sibling_and_original_group(self):
        value = native()
        group = {"objectId": "group", "transform": {
            "scaleX": 1, "scaleY": 1, "translateX": 30, "translateY": 20, "unit": "PT"},
            "elementGroup": {"children": value["slides"][1]["pageElements"]}}
        value["slides"][1]["pageElements"] = [group]
        expected = self.read(value)
        group["elementGroup"]["children"].append(empty_decoration())
        result = self.read(value)
        self.assertEqual(result["slides"], expected["slides"])
        self.assertEqual(result["native_source"], value)

    def test_empty_decoration_does_not_exempt_invalid_geometry_or_grouped_response_regions(self):
        mutations = [lambda e: e.pop("transform"),
                     lambda e: e["size"]["width"].update(magnitude=0),
                     lambda e: e["size"]["height"].update(unit="PX"),
                     lambda e: e["transform"].update(scaleY=float("nan")),
                     lambda e: e["transform"].update(scaleY=0),
                     lambda e: e["transform"].update(scaleX=1, scaleY=0)]
        for mutation in mutations:
            value = native(); node = empty_decoration(); mutation(node)
            value["slides"][1]["pageElements"].append(node)
            with self.subTest(mutation=mutation), self.assertRaises(native_teacher_source.Unreadable):
                self.read(value)
        value = native()
        value["slides"][1]["pageElements"].append({"objectId": "group", "transform": {
            "scaleX": 0, "scaleY": 1, "unit": "PT"}, "elementGroup": {
                "children": [empty_decoration(), shape("group-answer", "[[NOTES:X:Q]]")]}})
        with self.assertRaises(native_teacher_source.Unreadable):
            self.read(value)

    def test_missing_binding_metadata_or_tables_require_original_export(self):
        for mutation in (lambda e: e.update(description=response_binding.PREFIX + '{"kind":"picture-activity"}'),
                         lambda e: e.update(description=response_binding.PREFIX + '{}'),
                         lambda e: e.update(description=response_binding.PREFIX + 'invalid'),
                         lambda e: e.update(table={"rows": 1})):
            value = native(); mutation(value["slides"][0]["pageElements"][0])
            with self.subTest(value=value), self.assertRaises(native_teacher_source.Unreadable): self.read(value)

    def test_missing_or_invalid_geometry_text_and_duplicate_ids_fail_closed(self):
        mutations = [lambda e: e.pop("transform"), lambda e: e.update(transform={"scaleX": 0, "unit": "PT"}),
                     lambda e: e["size"]["width"].update(unit="PX"),
                     lambda e: e["size"]["width"].update(magnitude=float("inf")),
                     lambda e: e.update(size=None), lambda e: e.update(shape=None),
                     lambda e: e["shape"].update(text={"textElements": [None]}),
                     lambda e: e["shape"]["text"]["textElements"][0]["textRun"].update(content=42),
                     lambda e: e.update(description={}), lambda e: e.update(objectId="question")]
        for mutation in mutations:
            value = native(); mutation(value["slides"][0]["pageElements"][0])
            with self.subTest(mutation=mutation), self.assertRaises(native_teacher_source.Unreadable): self.read(value)

    def test_exact_identity_revision_and_complete_size_bounds_are_required(self):
        for value in ({**native(), "presentationId": "OTHER"}, {**native(), "slides": []},
                      {**native(), "slides": [None]}):
            with self.subTest(value=value), self.assertRaises(native_teacher_source.Unreadable): self.read(value)
        for limits in ({"max_slides": 1}, {"max_slide_bytes": 1}, {"max_deck_bytes": 1}):
            with self.subTest(limits=limits), self.assertRaises(native_teacher_source.Unreadable): self.read(**limits)
        with self.assertRaises(native_teacher_source.Unreadable):
            native_teacher_source.read(native(), presentation_id=TEMPLATE, revision="")


class TeacherFallbackTests(unittest.TestCase):
    def setUp(self):
        completion._native_teacher_templates.clear(); completion._templates.clear(); completion._results.clear()

    def test_export403_uses_exact_official_get_and_stable_revision_cache(self):
        with patch.object(completion, "_template", side_effect=google.GoogleError("Export denied", 403)), \
             patch.object(google, "drive_file", return_value=metadata()) as file, \
             patch.object(google, "native_presentation", return_value=native()) as get:
            first = completion._teacher_template("read", TEMPLATE)
            second = completion._teacher_template("read", TEMPLATE)
        get.assert_called_once_with("read", TEMPLATE)
        self.assertEqual(file.call_count, 3)
        self.assertIs(first[1], second[1])
        self.assertEqual(first[1]["native_source_revision"], "1")
        self.assertEqual(completion._templates, {})

    def test_readable_export_is_preferred_and_other_http_errors_never_fallback(self):
        expected = metadata(), source()
        with patch.object(completion, "_template", return_value=expected), patch.object(google, "native_presentation") as get:
            self.assertIs(completion._teacher_template("read", TEMPLATE), expected)
            get.assert_not_called()
        for status in (401, 404, 429, 500, None):
            with self.subTest(status=status), patch.object(completion, "_template", side_effect=google.GoogleError("Denied", status)), \
                 patch.object(google, "native_presentation") as get, self.assertRaises(google.GoogleError):
                completion._teacher_template("read", TEMPLATE)
            get.assert_not_called()

    def test_pptx_trash_identity_and_native_access_failure_cannot_borrow_a_source(self):
        for meta in (metadata(mimeType=google.PPTX), metadata(trashed=True), metadata(id="OTHER")):
            with self.subTest(meta=meta), patch.object(completion, "_template", side_effect=google.GoogleError("Export denied", 403)), \
                 patch.object(google, "drive_file", return_value=meta), patch.object(google, "native_presentation") as get, \
                 self.assertRaises(completion.Unreadable): completion._teacher_template("read", TEMPLATE)
            get.assert_not_called()
        with patch.object(completion, "_template", side_effect=google.GoogleError("Export denied", 403)), \
             patch.object(google, "drive_file", return_value=metadata()), \
             patch.object(google, "native_presentation", side_effect=google.GoogleError("Native denied", 403)), self.assertRaises(google.GoogleError):
            completion._teacher_template("read", TEMPLATE)
        self.assertEqual(completion._native_teacher_templates, {})

    def test_bookend_revision_drift_and_wrong_native_identity_are_not_cached(self):
        for values, returned in (([metadata(), metadata("2")], native()),
                                 ([metadata()], {**native(), "presentationId": "OTHER"})):
            with self.subTest(returned=returned), patch.object(completion, "_template", side_effect=google.GoogleError("Export denied", 403)), \
                 patch.object(google, "drive_file", side_effect=values), patch.object(google, "native_presentation", return_value=returned), \
                 self.assertRaises(completion.Unreadable): completion._teacher_template("read", TEMPLATE)
            self.assertEqual(completion._native_teacher_templates, {})

    def test_new_drive_version_reloads_native_even_when_modified_time_is_same(self):
        with patch.object(completion, "_template", side_effect=google.GoogleError("Export denied", 403)), \
             patch.object(google, "drive_file", side_effect=[metadata(), metadata(), metadata("2"), metadata("2")]), \
             patch.object(google, "native_presentation", return_value=native()) as get:
            self.assertEqual(completion._teacher_template("read", TEMPLATE)[1]["native_source_revision"], "1")
            self.assertEqual(completion._teacher_template("read", TEMPLATE)[1]["native_source_revision"], "2")
        self.assertEqual(get.call_count, 2)

    def test_submitted_reader403_never_uses_native_fallback(self):
        with patch.object(google, "deck_bytes", side_effect=google.GoogleError("Submitted export denied", 403)), \
             patch.object(google, "native_presentation") as get, self.assertRaises(google.GoogleError):
            completion._read("read", STUDENT, {"mimeType": completion.SLIDES})
        get.assert_not_called()

    def test_official_native_reader_uses_get_exact_encoded_id_and_existing_token(self):
        with patch.object(google, "get_json", return_value={}) as get:
            google.native_presentation("read", "exact/deck")
        get.assert_called_once_with("https://slides.googleapis.com/v1/presentations/exact%2Fdeck", "read", timeout=30)

    def test_native_teacher_geometry_matches_exported_copy_renamed_boxes_without_printed_credit(self):
        teacher = native_teacher_source.read(native(), presentation_id=TEMPLATE, revision="1")
        slides = [[("export_" + unit["key"], "An original answer." if unit["key"] == "answer" else unit["text"],
                    tuple(int(n) for n in unit["box"])) for unit in slide["units"]] for slide in teacher["slides"]]
        student = completion.read_deck(pptx(slides, size=tuple(int(n) for n in teacher["size"])))
        scored = completion.assess(student, teacher, found_codes_only=True)
        self.assertEqual(scored["notes_score"], 10)
        self.assertEqual(scored["counts"]["NOTES"]["required"], 1)
        self.assertEqual(scored["revision_score"], 0)
        self.assertEqual(completion.assess(teacher, teacher, found_codes_only=True)["notes_score"], 0)


class TeacherFoundCodePolicyTests(unittest.TestCase):
    def test_stale_count_is_advisory_outside_photo_without_inventing_eighth_question(self):
        teacher = source(header=HEADER.replace("NOTES=1", "NOTES=2"))
        before = deepcopy(teacher)
        loose = completion.teacher_readiness(teacher, revision="19", require_complete_codes=False)
        self.assertEqual(loose["status"], "READY")
        self.assertEqual(loose["declared_counts"]["NOTES"], 2)
        self.assertEqual(loose["missing_ids"], [])
        self.assertEqual(loose["teacher_coding_policy"], "found_codes")
        self.assertEqual(teacher, before)
        self.assertEqual(completion.teacher_readiness(teacher, require_complete_codes=True)["status"], "NOT_READY")

    def test_no_countable_codes_duplicate_or_mixed_cycle_sources_still_hold(self):
        duplicate = source(); duplicate["slides"][1]["units"].append(deepcopy(duplicate["slides"][1]["units"][-2]))
        mixed = source(); mixed["slides"][1]["units"][-1]["text"] = "[[DRAFT:OTHER:Q-MASTERY]]"
        optional = source(header="Lesson", codes=False)
        optional["slides"][1]["units"].append({"key": "optional", "text": "[[OPTIONAL:F06:TRY]]", "box": (0, 0, 10, 10)})
        zero_inventory = source(header=HEADER.replace("NOTES=1", "NOTES=0").replace("DRAFT=1", "DRAFT=0")
                                .replace("NOTES_IDS=Q-MASTERY", "NOTES_IDS=-").replace("DRAFT_IDS=Q-MASTERY", "DRAFT_IDS=-"), codes=False)
        for teacher in (source(header="Lesson", codes=False), zero_inventory, optional, duplicate, mixed):
            with self.subTest(teacher=teacher):
                result = completion.teacher_readiness(teacher, require_complete_codes=False)
                self.assertEqual(result["status"], "NOT_READY")
                self.assertNotIn("notes_score", result)

    def test_found_code_scoring_uses_submitted_denominator_with_sparse_teacher(self):
        teacher = source(header=HEADER.replace("NOTES=1", "NOTES=2"))
        with patch.object(completion, "student_coding_required", return_value=False), \
             patch.object(completion, "_teacher_template", return_value=(metadata(), teacher)), \
             patch.object(google, "drive_file", return_value={"id": STUDENT, "mimeType": google.PPTX, "version": "1"}), \
             patch.object(completion, "_read", return_value=source("An original answer.")):
            result = completion.score_file("read", fixture(), "forensics", "Wk 04", FORM, STUDENT, work_result())
        self.assertIsNone(result["problem"])
        self.assertEqual(result["notes_score"], 10)
        self.assertEqual(result["counts"]["NOTES"]["required"], 1)


if __name__ == "__main__":
    unittest.main()
