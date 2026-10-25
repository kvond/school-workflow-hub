"""Compact manifest compatibility from synthetic native marker fields only."""
from copy import deepcopy
import hashlib
import unittest
from unittest.mock import patch

from school_workflow_hub.adapters import assessment_provider as provider
from school_workflow_hub.workflows import assessment_records as records
from tests import test_assessment_records as fixtures
from tests import test_assessment_provider as provider_fixtures


NOTES_IDS = (
    "BELLRINGER-ALMOST", "CA1-SUN", "CA1-CASE", "CA1-RULE",
    "CA1-PATTERNBREAK", "CA1-MASTERY", "CA1-ACT-DISK", "CA2-IDEA",
    "CA2-RULE", "CA2-PATTERNBREAK", "CA2-TIERNOTES", "CA2-MASTERY",
    "CA2-ACT-WHERE", "WHATIF",
)
BANK_IDS = (
    "Photosynthesis", "Glucose", "Chlorophyll", "Chemical energy",
    "Light energy", "Oxygen", "Carbon dioxide", "Water",
)
DRAFT_IDS = ("CA1-MASTERY", "CA2-MASTERY", "WHATIF")
OPTIONAL_IDS = ("OPT-RELATES",)
COMPACT_HEADER = (
    "[[MARKER-INVENTORY:C05]] NOTES=14 DRAFT=3 OPTIONAL=1 BANK_TERMS=8 "
    "NOTES_IDS=" + ",".join(NOTES_IDS)
    + " DRAFT_IDS=" + ",".join(DRAFT_IDS)
    + " OPTIONAL_IDS=" + ",".join(OPTIONAL_IDS)
)


def _native_field(category, qid):
    return f"[[{category}:C05:{qid}]]\nFictional response {category} {qid}."


def compact_source():
    """One compact header and 26 unique category-specific synthetic fields."""
    native_notes = tuple(qid for qid in NOTES_IDS if qid != "CA1-ACT-DISK") + ("CA1-ACT-DISK",)
    groups = (("NOTES", native_notes), ("BANK", BANK_IDS),
              ("DRAFT", DRAFT_IDS), ("OPTIONAL", OPTIONAL_IDS))
    return COMPACT_HEADER + "\n" + "\n".join(
        _native_field(category, qid) for category, ids in groups for qid in ids)


def _with_header(header):
    return header + compact_source()[len(COMPACT_HEADER):]


def _versioned_source():
    header = "\n".join((
        "[[MARKER-INVENTORY:C05]]", "VERSION=synthetic-C05-r1",
        "NOTES=14 DRAFT=3 OPTIONAL=1 BANK_TERMS=8", "NOTES_IDS=", *NOTES_IDS,
        "DRAFT_IDS=", *DRAFT_IDS, "OPTIONAL_IDS=", *OPTIONAL_IDS,
        "BANK_IDS=", *BANK_IDS, "",
    ))
    return header + compact_source()[len(COMPACT_HEADER):]


def _synthetic_packet(source, manifest=None):
    item = fixtures.packet()
    item.update(class_key="biology", assignment_key="synthetic-compact-C05",
                section_id="synthetic-section", section_name="Synthetic section",
                stage_reference="Synthetic explicit final checkpoint.",
                feedback="Synthetic reviewed report; no student data.",
                comparison_status="Synthetic first assessment only.", ic_assignment_id="")
    item["identity"] = {
        "student_id": "", "student_id_kind": "synthetic_roster_key",
        "learner_key": "roster:" + "e" * 32, "display_name": "Fictional Learner",
        "identity_basis": "Synthetic fixture only.",
    }
    item["source"]["sha256"] = hashlib.sha256(source.encode()).hexdigest()
    item["manifest"] = deepcopy(manifest if manifest is not None else records.derive_manifest(source))
    item["responses"] = []
    for category, field in (("NOTES", "notes_ids"), ("BANK", "bank_ids"), ("DRAFT", "draft_ids")):
        for qid in item["manifest"][field]:
            item["responses"].append(fixtures.response(
                qid, category, "meaningful_attempt", f"Fictional response {category} {qid}."))
    for qid in item["manifest"]["optional_ids"]:
        item["responses"].append(fixtures.response(
            qid, "NOTES", "meaningful_attempt", f"Fictional response OPTIONAL {qid}.", optional=True))
    return item


class CompactManifestTests(unittest.TestCase):
    def assert_manifest_held(self, source):
        with self.assertRaisesRegex(ValueError, "assessment_manifest_unparsed"):
            records.derive_manifest(source)

    def test_missing_bank_list_uses_native_bank_order_and_preserves_declared_other_order(self):
        source = compact_source()
        self.assertNotIn(" BANK_IDS=", COMPACT_HEADER)
        self.assertNotIn("VERSION=", source)
        self.assertEqual(len(list(records._MARKER.finditer(source))), 26)
        manifest = records.derive_manifest(source)
        self.assertEqual(manifest["notes_ids"], list(NOTES_IDS))
        self.assertEqual(manifest["bank_ids"], list(BANK_IDS))
        self.assertEqual(manifest["draft_ids"], list(DRAFT_IDS))
        self.assertEqual(manifest["optional_ids"], list(OPTIONAL_IDS))
        self.assertEqual(manifest["inventory_line"], "[[MARKER-INVENTORY:C05]]")
        self.assertEqual(manifest["inventory_basis"], "marker_census")
        self.assertEqual(manifest["version_label"], "legacy-compact-manifest")
        self.assertIn("BANK_IDS was absent", manifest["reconciliation_note"])
        self.assertEqual(records.census_manifest(source)["notes_ids"][-1], "CA1-ACT-DISK")
        self.assertNotEqual(records.census_manifest(source)["notes_ids"], manifest["notes_ids"])
        item = _synthetic_packet(source)
        before = deepcopy(item)
        self.assertIs(records.validate_packet(item, source), item)
        self.assertEqual(item, before)

    def test_explicit_bank_ids_keep_their_declared_order_without_inference(self):
        for ids in (BANK_IDS, tuple(reversed(BANK_IDS))):
            with self.subTest(ids=ids):
                source = _with_header(COMPACT_HEADER + " BANK_IDS=" + ",".join(ids))
                manifest = records.derive_manifest(source)
                self.assertEqual(manifest["bank_ids"], list(ids))
                self.assertEqual(manifest["notes_ids"], list(NOTES_IDS))
                self.assertNotIn("BANK_IDS was absent", manifest["reconciliation_note"])
                item = _synthetic_packet(source)
                self.assertIs(records.validate_packet(item, source), item)
                self.assertEqual(item["manifest"]["bank_ids"], list(ids))

    def test_descriptive_checkpoints_metadata_before_id_lists_never_selects_a_stage(self):
        original_manifest = records.derive_manifest(compact_source())
        for description in ("UNVERIFIED; do not infer due dates.", "NOTES early; revisions follow the teacher's plan."):
            header = COMPACT_HEADER.replace(" NOTES_IDS=", " CHECKPOINTS=" + description + " NOTES_IDS=", 1)
            source = _with_header(header)
            self.assertEqual(records.derive_manifest(source), original_manifest)
            for stage in ("initial", "final"):
                with self.subTest(description=description, stage=stage):
                    item = _synthetic_packet(source)
                    item.update(stage=stage, stage_reference="Synthetic explicit " + stage + " checkpoint.")
                    before = deepcopy(item)
                    self.assertIs(records.validate_packet(item, source), item)
                    self.assertEqual(item, before)
                    self.assertEqual(records.completion(item)["counts"]["DRAFT"]["due"], stage == "final")

    def test_checkpoint_metadata_remains_bounded_nonempty_and_other_fields_stay_closed(self):
        for suffix in (" CHECKPOINTS=", " CHECKPOINTS=" + "x" * 401, " UNREVIEWED=synthetic"):
            with self.subTest(suffix=suffix):
                self.assert_manifest_held(_with_header(COMPACT_HEADER + suffix))

    def test_inferred_bank_order_follows_observed_fields_not_an_alphabetical_sort(self):
        source = compact_source().replace(_native_field("BANK", "Water"), "", 1)
        source = source.replace(_native_field("BANK", "Photosynthesis"),
                                _native_field("BANK", "Water") + "\n" + _native_field("BANK", "Photosynthesis"), 1)
        self.assertEqual(records.derive_manifest(source)["bank_ids"], ["Water", *BANK_IDS[:-1]])

    def test_same_question_id_in_notes_and_draft_remains_category_distinct(self):
        source = compact_source()
        item = _synthetic_packet(source)
        self.assertIs(records.validate_packet(item, source), item)
        for qid in DRAFT_IDS:
            self.assertIn(qid, item["manifest"]["notes_ids"])
            self.assertIn(qid, item["manifest"]["draft_ids"])
            self.assertEqual(sum(r["question_id"] == qid for r in item["responses"]), 2)

    def test_declared_counts_must_match_every_native_category(self):
        for old, new in (("NOTES=14", "NOTES=15"), ("DRAFT=3", "DRAFT=2"),
                         ("OPTIONAL=1", "OPTIONAL=0"), ("BANK_TERMS=8", "BANK_TERMS=7")):
            with self.subTest(field=old):
                self.assert_manifest_held(_with_header(COMPACT_HEADER.replace(old, new, 1)))

    def test_declared_nonbank_sets_cannot_silently_replace_observed_ids(self):
        for old, new in (("NOTES_IDS=BELLRINGER-ALMOST", "NOTES_IDS=OTHER-NOTE"),
                         ("DRAFT_IDS=CA1-MASTERY", "DRAFT_IDS=OTHER-DRAFT"),
                         ("OPTIONAL_IDS=OPT-RELATES", "OPTIONAL_IDS=OTHER-OPTIONAL")):
            with self.subTest(field=old):
                self.assert_manifest_held(_with_header(COMPACT_HEADER.replace(old, new, 1)))
        explicit = COMPACT_HEADER + " BANK_IDS=" + ",".join((*BANK_IDS[:-1], "Other term"))
        self.assert_manifest_held(_with_header(explicit))

    def test_duplicate_count_fields_are_rejected_even_when_values_agree(self):
        for repeated in ("NOTES=14", "NOTES=15", "DRAFT=3", "OPTIONAL=1", "BANK_TERMS=8"):
            with self.subTest(field=repeated):
                self.assert_manifest_held(_with_header(COMPACT_HEADER + " " + repeated))

    def test_duplicate_id_fields_are_rejected_even_when_values_agree(self):
        fields = (("NOTES_IDS", NOTES_IDS), ("DRAFT_IDS", DRAFT_IDS), ("OPTIONAL_IDS", OPTIONAL_IDS))
        for name, ids in fields:
            with self.subTest(field=name):
                self.assert_manifest_held(_with_header(COMPACT_HEADER + " " + name + "=" + ",".join(ids)))
        bank = " BANK_IDS=" + ",".join(BANK_IDS)
        self.assert_manifest_held(_with_header(COMPACT_HEADER + bank + bank))

    def test_duplicate_or_conflicting_inventory_headers_never_become_one_census(self):
        for repeated in ("[[MARKER-INVENTORY:C05]]", "[[MARKER-INVENTORY:C06]]", COMPACT_HEADER):
            with self.subTest(repeated=repeated):
                self.assert_manifest_held(compact_source() + "\n" + repeated)

    def test_repeated_native_fields_within_one_category_are_rejected(self):
        for category, qid in (("NOTES", NOTES_IDS[0]), ("DRAFT", DRAFT_IDS[0]),
                              ("OPTIONAL", OPTIONAL_IDS[0]), ("BANK", BANK_IDS[0])):
            with self.subTest(category=category):
                self.assert_manifest_held(compact_source() + "\n" + _native_field(category, qid))

    def test_foreign_or_mixed_marker_cycles_are_rejected(self):
        for source in (compact_source().replace("[[BANK:C05:", "[[BANK:C06:", 1),
                       compact_source().replace(":C05:", ":C06:"),
                       compact_source() + "\n[[NOTES:C06:FOREIGN]]\nSynthetic foreign field."):
            with self.subTest(source=source):
                self.assert_manifest_held(source)

    def test_malformed_native_tags_cannot_be_ignored_as_extra_text(self):
        malformed = ("[[BANK:C05:Water]", "[[BANK:C05:bad/path]]", "[[NOTES:C05:bad/path]]",
                     "[[DRAFT:C05:]]", "[[OPTIONAL:C05:bad!id]]", "[[BANK::Water]]")
        for tag in malformed:
            with self.subTest(tag=tag):
                self.assert_manifest_held(compact_source() + "\n" + tag)

    def test_required_nonbank_id_fields_cannot_be_inferred_from_counts_or_tags(self):
        for name, ids in (("NOTES_IDS", NOTES_IDS), ("DRAFT_IDS", DRAFT_IDS), ("OPTIONAL_IDS", OPTIONAL_IDS)):
            with self.subTest(field=name):
                header = COMPACT_HEADER.replace(" " + name + "=" + ",".join(ids), "", 1)
                self.assert_manifest_held(_with_header(header))

    def test_optional_marker_set_is_checked_independently(self):
        source = compact_source().replace(_native_field("OPTIONAL", OPTIONAL_IDS[0]), "", 1)
        self.assert_manifest_held(source)
        self.assert_manifest_held(compact_source() + "\n" + _native_field("OPTIONAL", "OPT-EXTRA"))
        item = _synthetic_packet(compact_source())
        optional = next(r for r in item["responses"] if r["optional"])
        optional["optional"] = False
        with self.assertRaisesRegex(ValueError, "assessment_response_outside_manifest"):
            records.validate_packet(item, compact_source())

    def test_duplicate_declared_ids_are_not_rescued_by_a_matching_count(self):
        for old, new in (("NOTES_IDS=BELLRINGER-ALMOST,CA1-SUN", "NOTES_IDS=BELLRINGER-ALMOST,BELLRINGER-ALMOST"),
                         ("DRAFT_IDS=CA1-MASTERY,CA2-MASTERY", "DRAFT_IDS=CA1-MASTERY,CA1-MASTERY")):
            with self.subTest(field=old):
                self.assert_manifest_held(_with_header(COMPACT_HEADER.replace(old, new, 1)))

    def test_zero_bank_compact_headers_keep_the_existing_empty_bank_contract(self):
        source = compact_source()
        for qid in BANK_IDS:
            source = source.replace(_native_field("BANK", qid), "", 1)
        source = source.replace("BANK_TERMS=8", "BANK_TERMS=0", 1)
        for explicit in (False, True):
            candidate = source.replace("\n", " BANK_IDS=-\n", 1) if explicit else source
            with self.subTest(explicit=explicit):
                manifest = records.derive_manifest(candidate)
                self.assertEqual(manifest["bank_ids"], [])
                self.assertEqual(manifest["notes_ids"], list(NOTES_IDS))
                item = _synthetic_packet(candidate)
                self.assertIs(records.validate_packet(item, candidate), item)
                self.assertEqual(item["manifest"]["bank_ids"], [])

    def test_markerless_zero_count_header_remains_held_before_any_provider_or_state_use(self):
        source = ('[[MARKER-INVENTORY:C05]] NOTES=0 DRAFT=0 OPTIONAL=0 BANK_TERMS=0 '
                  'NOTES_IDS=- DRAFT_IDS=- OPTIONAL_IDS=-')
        with self.assertRaisesRegex(ValueError, 'assessment_manifest_missing'):
            records.derive_manifest(source)
        with provider_fixtures.drafting('{}') as draft, \
             patch.object(provider.assessment_save, 'capture', return_value=dict(fixtures.CAPTURE, text=source)), \
             patch.object(provider, '_teacher_prompts') as prompts:
            with self.assertRaisesRegex(ValueError, 'assessment_manifest_missing'):
                provider.draft(None, provider_fixtures.request(), provider_fixtures.CONFIG)
        prompts.assert_not_called()
        self.assertEqual(draft.calls, 0); self.assertEqual(draft.port.rows, {})

    def test_client_supplied_census_cannot_bypass_a_broken_compact_header(self):
        source = _with_header(COMPACT_HEADER.replace("NOTES=14", "NOTES=15", 1))
        census = records.census_manifest(source)
        item = _synthetic_packet(source, census)
        with self.assertRaisesRegex(ValueError, "assessment_manifest_unparsed|assessment_manifest_not_in_source"):
            records.validate_packet(item, source)

    def test_versioned_client_census_must_still_match_literal_native_fields(self):
        source = fixtures.SOURCE + "\n[[NOTES:T01:N1]]\n"
        item = fixtures.packet()
        item["identity"].update(student_id="", student_id_kind="synthetic_roster_key",
                                learner_key="roster:" + "e" * 32, display_name="Fictional Learner",
                                identity_basis="Synthetic fixture only.")
        item["manifest"].update(inventory_basis="marker_census",
                                reconciliation_note="Synthetic prior documented census.",
                                version_label="prior-preserved-label")
        with self.assertRaisesRegex(ValueError, "assessment_manifest_not_in_source"):
            records.validate_packet(item, source)

    def test_versioned_partial_native_census_cannot_shrink_the_declared_requirements(self):
        source = _versioned_source().replace(_native_field("BANK", "Water"), "", 1)
        item = _synthetic_packet(source, records.census_manifest(source))
        self.assertEqual(len(item["manifest"]["bank_ids"]), 7)
        self.assertEqual(len(records.derive_manifest(source)["bank_ids"]), 8)
        with self.assertRaisesRegex(ValueError, "assessment_manifest_not_in_source"):
            records.validate_packet(item, source)

    def test_versioned_extra_native_census_cannot_expand_the_declared_requirements(self):
        source = _versioned_source() + "\n" + _native_field("NOTES", "EXTRA-NOTE")
        item = _synthetic_packet(source, records.census_manifest(source))
        self.assertEqual(len(item["manifest"]["notes_ids"]), 15)
        self.assertEqual(len(records.derive_manifest(source)["notes_ids"]), 14)
        with self.assertRaisesRegex(ValueError, "assessment_manifest_not_in_source"):
            records.validate_packet(item, source)

    def test_versioned_census_matching_both_header_and_native_fields_stays_valid(self):
        source = _versioned_source()
        manifest = records.census_manifest(source)
        manifest.update(version_label="prior-preserved-census-label",
                        reconciliation_note="Synthetic prior documented complete census.")
        item = _synthetic_packet(source, manifest)
        before = deepcopy(item)
        self.assertIs(records.validate_packet(item, source), item)
        self.assertEqual(item, before)

    def test_valid_compact_census_keeps_prior_documented_label_and_note(self):
        source = compact_source()
        manifest = records.derive_manifest(source)
        manifest.update(version_label="prior-preserved-census-label",
                        reconciliation_note="Synthetic prior documented census; retain this exact note.")
        item = _synthetic_packet(source, manifest)
        before = deepcopy(item)
        self.assertIs(records.validate_packet(item, source), item)
        self.assertEqual(item, before)


if __name__ == "__main__":
    unittest.main()
