"""Assessment packet validation, arithmetic and row building: synthetic data only."""
import unittest

from school_workflow_hub.workflows import assessment_records as ar

HEADER = ("Test manifest\n"
          "[[MARKER-INVENTORY:T01]]\n"
          "VERSION=T01-test-2026-09-01-r1\n"
          "NOTES=2  DRAFT=1  OPTIONAL=1  BANK_TERMS=1\n"
          "Required: NOTES, DRAFT, BANK. Optional: OPTIONAL.\n"
          "NOTES_IDS=\n" "N1\nN2\n"
          "DRAFT_IDS=\n" "D1\n"
          "OPTIONAL_IDS=\n" "OPT1\n"
          "PERMITTED_PAIRS=\n" "D1\n" "N2: NOTES-only.\n"
          "BANK_IDS=\n" "Term one\n"
          "CHECKPOINTS=UNVERIFIED; do not infer due dates.\n")
SOURCE = (HEADER +
          "[[MARKER-INVENTORY:T01]] VERSION=T01-test-2026-09-01-r1\n"
          "N1 What did you notice?\nanswer one in my own words\n"
          "N2 Explain the rule.\n\n"
          "B1 Term one\nbank answer own words\n"
          "D1 Revise your claim.\ndraft attempt pasted text\n"
          "OPT1 Optional connection\noptional attempt words\n")


def packet(**overrides):
    base = {
        "schema_version": ar.PACKET_VERSION, "class_key": "biology",
        "section_id": "26716", "section_name": "A4 Biology",
        "assignment_key": "BIO-C02-F01", "stage": "final",
        "stage_reference": "teacher plan 2026-09-16: Write Revisions and turn in",
        "identity": {"student_id": "90522", "student_id_kind": "local_student_number",
                     "learner_key": "", "display_name": "Test Student",
                     "identity_basis": "deck alias matched to live section roster"},
        "source": {"file_id": "f" * 24, "url": "https://docs.google.com/presentation/d/" + "f" * 24 + "/edit",
                   "modified_at": "2026-09-16T18:04:34.983Z", "version": "41",
                   "sha256": "a" * 64},
        "manifest": {"inventory_line": "[[MARKER-INVENTORY:T01]]",
                     "version_label": "VERSION=T01-test-2026-09-01-r1",
                     "notes_ids": ["N1", "N2"], "bank_ids": ["Term one"],
                     "draft_ids": ["D1"], "optional_ids": ["OPT1"]},
        "responses": [
            response("N1", "NOTES", "meaningful_attempt", "answer one in my own words"),
            response("N2", "NOTES", "blank", "", conceptual="Evidence unavailable",
                     explanation="Evidence unavailable"),
            response("Term one", "BANK", "meaningful_attempt", "bank answer own words"),
            response("D1", "DRAFT", "printed_copy", "draft attempt pasted text"),
            response("OPT1", "NOTES", "meaningful_attempt", "optional attempt words",
                     optional=True),
        ],
        "feedback": "Current understanding: developing.\nOne question: why?",
        "comparison_status": "One submitted snapshot; no dated growth comparison",
        "ic_assignment_id": "IC_RESOURCE:1;CODE:T01F",
    }
    base.update(overrides)
    return base


def response(qid, category, judgment, excerpt, *, conceptual="Developing",
             explanation="Names an idea", optional=False):
    return {"question_id": qid, "category": category, "judgment": judgment,
            "response_excerpt": excerpt, "evidence_note": "slide", "prompt": qid + " prompt",
            "prompt_basis": "deck", "conceptual_level": conceptual,
            "explanation_level": explanation, "conceptual_feedback": "feedback text",
            "slide_number": "3", "optional": optional}


CAPTURE = {"schema_version": "hub-assessment-capture/v1",
           "file": {"id": "f" * 24, "name": "Test deck", "mime_type": "p",
                    "modified_at": "2026-09-16T18:04:34.983Z", "version": "41"},
           "text": SOURCE, "sha256": "a" * 64, "captured_at": "2026-09-18T15:00:00+00:00"}


class ValidationTests(unittest.TestCase):
    def test_valid_packet_passes_against_its_source(self):
        item = packet()
        self.assertIs(ar.validate_packet(item, SOURCE), item)

    def test_evidence_must_quote_the_captured_text(self):
        bad = packet()
        bad["responses"][0]["response_excerpt"] = "a paraphrase, not a quote"
        with self.assertRaises(ValueError) as caught:
            ar.validate_packet(bad, SOURCE)
        self.assertEqual(str(caught.exception), "assessment_evidence_not_in_source")

    def test_blank_judgment_cannot_carry_an_excerpt(self):
        bad = packet()
        bad["responses"][1]["response_excerpt"] = "something"
        with self.assertRaises(ValueError) as caught:
            ar.validate_packet(bad, SOURCE)
        self.assertEqual(str(caught.exception), "assessment_blank_with_excerpt")

    def test_inventory_and_version_label_must_be_in_the_source(self):
        bad = packet()
        bad["manifest"] = dict(bad["manifest"], version_label="VERSION=OTHER-r9")
        with self.assertRaises(ValueError) as caught:
            ar.validate_packet(bad, SOURCE)
        self.assertEqual(str(caught.exception), "assessment_manifest_not_in_source")

    def test_responses_outside_the_manifest_are_rejected(self):
        bad = packet()
        bad["responses"].append(response("N9", "NOTES", "blank", "",
                                         conceptual="Evidence unavailable",
                                         explanation="Evidence unavailable"))
        with self.assertRaises(ValueError) as caught:
            ar.validate_packet(bad, SOURCE)
        self.assertEqual(str(caught.exception), "assessment_response_outside_manifest")

    def test_identity_requires_a_student_id_or_roster_learner_key(self):
        bad = packet()
        bad["identity"] = dict(bad["identity"], student_id="", learner_key="")
        with self.assertRaises(ValueError) as caught:
            ar.validate_packet(bad, SOURCE)
        self.assertEqual(str(caught.exception), "assessment_identity_unresolved")
        ok = packet()
        ok["identity"] = dict(ok["identity"], student_id="",
                              learner_key="roster:" + "1a" * 8)
        ar.validate_packet(ok, SOURCE)

    def test_level_vocabulary_is_closed(self):
        bad = packet()
        bad["responses"][0]["conceptual_level"] = "Excellent"
        with self.assertRaises(ValueError):
            ar.validate_packet(bad, SOURCE)

    def test_four_level_scale_with_named_critical_aspect_is_accepted(self):
        item = packet()
        for level in ("Beginning", "Extending"):
            item["responses"][0].update(conceptual_level=level, explanation_level="",
                                        critical_aspect="Producers as the entry point")
            self.assertIs(ar.validate_packet(item, SOURCE), item)

    def test_older_three_level_packets_stay_valid(self):
        item = packet()
        item["responses"][0]["conceptual_level"] = "Almost there"
        self.assertIs(ar.validate_packet(item, SOURCE), item)

    def test_critical_aspect_is_one_bounded_line_and_no_other_key_is_added(self):
        for bad_value in ("two\nlines", "x" * 201):
            bad = packet()
            bad["responses"][0]["critical_aspect"] = bad_value
            with self.assertRaises(ValueError):
                ar.validate_packet(bad, SOURCE)
        bad = packet()
        bad["responses"][0]["unexpected"] = "value"
        with self.assertRaises(ValueError):
            ar.validate_packet(bad, SOURCE)


class CompletionTests(unittest.TestCase):
    def test_final_stage_arithmetic_matches_the_policy(self):
        result = ar.completion(packet())
        content = result["scores"]["content_notes"]
        self.assertEqual(content["exact_value"], "20/3")  # 10*(1+1)/(2+1)
        revision = result["scores"]["revision"]
        self.assertEqual(revision["exact_value"], "0")  # printed_copy earns nothing
        self.assertEqual(result["counts"]["BANK"], {"completed": 1, "required": 1, "due": True})

    def test_a_review_judgment_blocks_the_save(self):
        held = packet()
        held["responses"][0]["judgment"] = "review"
        with self.assertRaises(ValueError) as caught:
            ar.completion(held)
        self.assertEqual(str(caught.exception), "assessment_not_ready")

    def test_record_id_binds_version_stage_policy_and_reviewed_content(self):
        base = ar.record_id(packet())
        self.assertTrue(base.startswith("asmt-") and len(base) == 29)
        self.assertEqual(base, ar.record_id(packet()))
        changed = packet()
        changed["source"] = dict(changed["source"], sha256="b" * 64)
        self.assertNotEqual(base, ar.record_id(changed))
        self.assertNotEqual(base, ar.record_id(packet(stage="initial")))
        rereviewed = packet()
        rereviewed["responses"][0]["judgment"] = "printed_copy"
        self.assertNotEqual(base, ar.record_id(rereviewed))
        reworded = packet(feedback="A different report.")
        self.assertNotEqual(base, ar.record_id(reworded))

    def test_manifest_derives_from_the_deck_header(self):
        manifest = ar.derive_manifest(SOURCE)
        self.assertEqual(manifest["inventory_line"], "[[MARKER-INVENTORY:T01]]")
        self.assertEqual(manifest["version_label"], "VERSION=T01-test-2026-09-01-r1")
        self.assertEqual(manifest["notes_ids"], ["N1", "N2"])
        self.assertEqual(manifest["bank_ids"], ["Term one"])
        self.assertEqual(manifest["draft_ids"], ["D1"])
        self.assertEqual(manifest["optional_ids"], ["OPT1"])

    def test_headerless_or_inconsistent_decks_are_manual_cases(self):
        with self.assertRaises(ValueError) as caught:
            ar.derive_manifest("no manifest anywhere\nN1 answer\n")
        self.assertEqual(str(caught.exception), "assessment_manifest_missing")
        broken = SOURCE.replace("NOTES=2", "NOTES=3", 1)
        with self.assertRaises(ValueError) as caught:
            ar.derive_manifest(broken)
        self.assertEqual(str(caught.exception), "assessment_manifest_unparsed")


class RowTests(unittest.TestCase):
    def test_rows_carry_fresh_capture_metadata_and_policy_fields(self):
        item = packet()
        rows = ar.build_rows(item, ar.completion(item), CAPTURE,
                             now="2026-09-18T15:05:00+00:00", prior_score_exact="60/13")
        submission = rows["submission"]
        self.assertEqual(submission["status"], "assessed")
        self.assertEqual(submission["source_sha256"], "a" * 64)
        self.assertEqual(submission["source_revision"], "41")
        self.assertEqual(submission["checked_at"], "2026-09-18T15:05:00+00:00")
        self.assertEqual(submission["checked_date"], "2026-09-18")
        self.assertEqual(submission["content_score_exact"], "20/3")
        self.assertEqual(submission["prior_score_exact"], "60/13")
        self.assertEqual(submission["notes_completed"], "1")
        self.assertEqual(submission["drafts_required"], "1")
        self.assertEqual(submission["ic_posting_status"], "unposted")
        self.assertEqual(submission["superseded_by"], "")
        self.assertEqual(len(rows["answers"]), 5)
        first = rows["answers"][0]
        self.assertEqual((first["question_id"], first["stage"]), ("N1", "NOTES"))
        self.assertEqual(first["meaningful_complete"], "TRUE")
        self.assertEqual(first["counts_toward_current_score"], "TRUE")
        draft = next(a for a in rows["answers"] if a["question_id"] == "D1")
        self.assertEqual(draft["meaningful_complete"], "FALSE")
        optional = next(a for a in rows["answers"] if a["question_id"] == "OPT1")
        self.assertEqual(optional["counts_toward_current_score"], "FALSE")
        self.assertEqual(first["critical_aspect"], "")

    def test_rows_carry_the_named_critical_aspect(self):
        item = packet()
        item["responses"][0]["critical_aspect"] = "Where transferred energy goes"
        rows = ar.build_rows(item, ar.completion(item), CAPTURE, now="2026-09-18T15:05:00+00:00")
        self.assertEqual(rows["answers"][0]["critical_aspect"], "Where transferred energy goes")

    def test_rows_refuse_a_capture_with_a_different_hash(self):
        item = packet()
        with self.assertRaises(ValueError):
            ar.build_rows(item, ar.completion(item), dict(CAPTURE, sha256="b" * 64),
                          now="2026-09-18T15:05:00+00:00")

    def test_aligned_cells_follow_live_header_order(self):
        headers = ["record_id", "", "content_score", "feedback"]
        cells = ar.aligned_cells(headers, {"record_id": "asmt-x", "content_score": 7.5,
                                           "feedback": "text"},
                                 numeric=("content_score",))["values"]
        self.assertEqual(cells[0]["userEnteredValue"], {"stringValue": "asmt-x"})
        self.assertEqual(cells[1]["userEnteredValue"], {"stringValue": ""})
        self.assertEqual(cells[2]["userEnteredValue"], {"numberValue": 7.5})
        with self.assertRaises(ValueError):
            ar.aligned_cells(headers, {"record_id": "x", "unknown_column": "y"})
        blank_score = ar.aligned_cells(headers, {"record_id": "x", "content_score": None},
                                       numeric=("content_score",))["values"]
        self.assertEqual(blank_score[2]["userEnteredValue"], {"stringValue": ""})

    def test_summary_row_is_bounded(self):
        row = ar.summary_row({"class_key": "biology", "section_id": "26716",
                              "section_name": "A4 Biology", "summary": "Honest text.",
                              "coverage": "5 verified of 24 files"},
                             now="2026-09-18T15:05:00+00:00")
        self.assertEqual(row["checked_at"], "2026-09-18T15:05:00+00:00")
        with self.assertRaises(ValueError):
            ar.summary_row({"class_key": "chemistry", "section_id": "1",
                            "section_name": "x", "summary": "s", "coverage": "c"},
                           now="2026-09-18T15:05:00+00:00")


if __name__ == "__main__":
    unittest.main()


CENSUS_SOURCE = ("Pre-manifest deck\n"
                 "[[NOTES:C02:Q-ONE]]\nfirst answer words\n"
                 "[[NOTES:C02:Q-TWO]]\n\n"
                 "[[NOTES:C02:Q-ONE]]\nduplicate marker box\n")


class CensusTests(unittest.TestCase):
    def test_headerless_decks_fall_back_to_a_documented_census(self):
        manifest = ar.derive_manifest(CENSUS_SOURCE)
        self.assertEqual(manifest["inventory_basis"], "marker_census")
        self.assertEqual(manifest["notes_ids"], ["Q-ONE", "Q-TWO"])
        self.assertEqual(manifest["bank_ids"], [])
        self.assertIn("duplicated marker", manifest["reconciliation_note"])
        self.assertIn("manual-reconciliation", manifest["reconciliation_note"])

    def test_census_packets_are_reverified_against_the_capture(self):
        manifest = ar.derive_manifest(CENSUS_SOURCE)
        item = packet()
        item["manifest"] = manifest
        item["responses"] = [
            response("Q-ONE", "NOTES", "meaningful_attempt", "first answer words"),
            response("Q-TWO", "NOTES", "blank", "", conceptual="Evidence unavailable",
                     explanation="Evidence unavailable"),
        ]
        self.assertIs(ar.validate_packet(item, CENSUS_SOURCE), item)
        result = ar.completion(item)
        self.assertEqual(result["scores"]["content_notes"]["exact_value"], "5")
        # Zero DRAFT denominator is N/A even at the final checkpoint.
        self.assertEqual(result["scores"]["revision"]["status"], "N/A")
        rows = ar.build_rows(item, result, dict(CAPTURE, text=CENSUS_SOURCE),
                             now="2026-09-18T18:00:00+00:00")
        self.assertIn("manual-reconciliation", rows["submission"]["reconciliation_basis"])
        tampered = dict(item, manifest=dict(manifest, notes_ids=["Q-ONE"]))
        with self.assertRaises(ValueError) as caught:
            ar.validate_packet(tampered, CENSUS_SOURCE)
        self.assertEqual(str(caught.exception), "assessment_manifest_not_in_source")

    def test_markerless_text_is_still_a_manual_hold(self):
        with self.assertRaises(ValueError) as caught:
            ar.derive_manifest("plain notes with no markers at all")
        self.assertEqual(str(caught.exception), "assessment_manifest_missing")
