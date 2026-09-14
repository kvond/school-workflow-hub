from dataclasses import asdict, replace
import json
from pathlib import Path
import unittest

from school_workflow_hub.core.models import AssignmentRecord
from school_workflow_hub.workflows.schoology_gradebook import (
    LINK_SURFACES, DesiredPlatformAssignment, LinkOccurrence, ObservedPlatformAssignment,
    UnsafeLink, audit_link_occurrences, canonicalize_url, load_manifest, reconcile_platform_assignments,
)


ROOT = Path(__file__).resolve().parents[1]


def desired_one():
    assignment = AssignmentRecord("synthetic:assignment-one", "synthetic:forensics", "Synthetic Evidence", "2026-09-18", "synthetic:deck-one", external_ids={"schoology": "synthetic:page-one", "infinite_campus": "synthetic:ic-one"})
    return DesiredPlatformAssignment(assignment, "week-03", "mp-01", "https://example.invalid/slides/deck-one/copy", "synthetic:folder-one", "draft", "synthetic:ic-one", "synthetic:r2")


def observations_for(desired):
    common = {"title": desired.assignment.title, "due_date": desired.assignment.intended_due_date, "deck_reference": desired.deck_reference}
    return (
        ObservedPlatformAssignment(desired.assignment.stable_id, desired.assignment.class_stable_id, "schoology", "synthetic:page-one", "synthetic:current-r1", dict(common, week_key=desired.week_key, marking_period=desired.marking_period, destination_id=desired.schoology_destination_id, publication=desired.expected_publication)),
        ObservedPlatformAssignment(desired.assignment.stable_id, desired.assignment.class_stable_id, "infinite_campus", "synthetic:ic-one", "synthetic:current-r1", common),
    )


class PlatformReconcileTests(unittest.TestCase):
    def test_templates_preserve_every_unknown_live_identifier(self):
        for course in ("anatomy", "forensics"):
            manifest = load_manifest(ROOT / "config" / "manifests" / f"{course}.json")
            self.assertEqual(manifest.class_stable_id, "UNVERIFIED")
            self.assertEqual(manifest.assignments[0].assignment.external_ids, {"schoology": "UNVERIFIED", "infinite_campus": "UNVERIFIED"})
            preview = reconcile_platform_assignments(manifest.assignments, ())
            self.assertFalse(preview.changes)
            self.assertEqual(preview.issues[0].code, "unverified_assignment_identity")

    def test_already_correct_and_canonical_equivalent_are_no_op(self):
        desired = desired_one()
        current = observations_for(desired)
        current = (replace(current[0], fields=dict(current[0].fields, deck_reference=desired.deck_reference + "?utm_source=fixture")), current[1])
        preview = reconcile_platform_assignments((desired,), current)
        self.assertFalse(preview.changes)
        self.assertFalse(preview.issues)

    def test_changed_due_date_and_link_have_reviewable_deterministic_evidence(self):
        desired = desired_one()
        current = observations_for(desired)
        current = (replace(current[0], fields=dict(current[0].fields, due_date="2026-09-15", deck_reference="https://example.invalid/slides/old/copy")), current[1])
        first = reconcile_platform_assignments((desired,), current)
        second = reconcile_platform_assignments((desired,), tuple(reversed(current)))
        self.assertEqual(first, second)
        self.assertEqual({c.field for c in first.changes}, {"deck_reference", "due_date"})
        for change in first.changes:
            self.assertEqual(change.approval_status, "pending")
            self.assertTrue(change.reversible)
            self.assertEqual(change.confidence, 1.0)
            self.assertEqual(len(change.evidence), 2)
        self.assertEqual(first.external_writes, 0)

    def test_missing_duplicate_incorrect_and_unavailable_targets_are_not_guessed(self):
        desired = desired_one()
        current = observations_for(desired)
        for input_rows, expected in (
            ((), "missing_schoology_target"),
            ((current[0], current[0], current[1]), "duplicate_schoology_target"),
            ((replace(current[0], target_id="synthetic:wrong"), current[1]), "incorrect_schoology_target"),
            ((replace(current[0], login_status="unavailable"), current[1]), "schoology_login_unavailable"),
        ):
            preview = reconcile_platform_assignments((desired,), input_rows)
            self.assertFalse(preview.changes)
            self.assertIn(expected, {i.code for i in preview.issues})

    def test_unknown_date_does_not_propose_erasure_and_wrong_course_is_flagged(self):
        desired = desired_one()
        current = observations_for(desired)
        unknown = replace(desired, assignment=replace(desired.assignment, intended_due_date=None))
        preview = reconcile_platform_assignments((unknown,), current)
        self.assertFalse(preview.changes)
        self.assertTrue(any(i.code == "unverified_infinite_campus_due_date" for i in preview.issues))
        preview = reconcile_platform_assignments((desired,), (replace(current[0], class_stable_id="synthetic:other"), current[1]))
        self.assertIn("stale_or_wrong_course_observation", {i.code for i in preview.issues})

    def test_private_values_are_rejected_before_preview(self):
        desired = desired_one()
        private = replace(desired, assignment=replace(desired.assignment, title="person" + "@" + "example.invalid"))
        with self.assertRaises(ValueError):
            reconcile_platform_assignments((private,), ())

    def test_duplicate_target_binding_and_conflicting_ic_reference_enter_issues(self):
        desired = desired_one()
        other = replace(desired, assignment=replace(desired.assignment, stable_id="synthetic:assignment-two"))
        preview = reconcile_platform_assignments((desired, other), observations_for(desired))
        self.assertFalse(preview.changes)
        self.assertIn("duplicate_schoology_target_binding", {i.code for i in preview.issues})
        preview = reconcile_platform_assignments((replace(desired, ic_linkage_id="synthetic:other-ic"),), observations_for(desired))
        self.assertIn("conflicting_infinite_campus_linkage", {i.code for i in preview.issues})

    def test_observed_target_claimed_by_other_assignment_or_course_blocks_changes(self):
        desired = desired_one()
        for system_index, system in enumerate(("schoology", "infinite_campus")):
            for conflicting_key in (
                {"assignment_key": "synthetic:other-assignment"},
                {"class_stable_id": "synthetic:other-course"},
            ):
                with self.subTest(system=system, conflicting_key=conflicting_key):
                    rows = list(observations_for(desired))
                    rows[system_index] = replace(rows[system_index], fields=dict(rows[system_index].fields, due_date="2026-09-15"))
                    rows.append(replace(rows[system_index], **conflicting_key))
                    first = reconcile_platform_assignments((desired,), tuple(rows))
                    self.assertFalse(first.changes)
                    self.assertIn(f"conflicting_observed_{system}_target_binding", {i.code for i in first.issues})
                    self.assertEqual(first, reconcile_platform_assignments((desired,), tuple(reversed(rows))))

    def test_target_identifiers_are_scoped_to_platform(self):
        desired = desired_one()
        shared_target = "synthetic:shared-external-id"
        desired = replace(desired, assignment=replace(desired.assignment, external_ids={"schoology": shared_target, "infinite_campus": shared_target}), ic_linkage_id=shared_target)
        rows = tuple(replace(row, target_id=shared_target, fields=dict(row.fields, due_date="2026-09-15")) for row in observations_for(desired))
        preview = reconcile_platform_assignments((desired,), rows)
        self.assertEqual({change.target_system for change in preview.changes}, {"schoology", "infinite_campus"})
        self.assertFalse(preview.issues)

    def test_explicit_unknown_old_values_never_become_reversible_changes(self):
        desired = desired_one()
        rows = tuple(replace(row, fields={key: "UNVERIFIED" for key in row.fields}) for row in observations_for(desired))
        for strict in (False, True):
            with self.subTest(require_observed_fields=strict):
                preview = reconcile_platform_assignments((desired,), rows, require_observed_fields=strict)
                self.assertFalse(preview.changes)
                self.assertEqual(len(preview.issues), 10)
                self.assertTrue(all(issue.code.startswith("unverified_observed_") for issue in preview.issues))

    def test_copy_edit_mode_mismatch_is_not_canonicalized_away(self):
        desired = desired_one()
        rows = observations_for(desired)
        rows = (replace(rows[0], fields=dict(rows[0].fields, deck_reference=desired.deck_reference.removesuffix("copy") + "edit")), rows[1])
        preview = reconcile_platform_assignments((desired,), rows)
        self.assertEqual(len(preview.changes), 1)
        self.assertEqual(preview.changes[0].field, "deck_reference")
        self.assertTrue(preview.changes[0].old_value.endswith("/edit"))
        self.assertTrue(preview.changes[0].proposed_value.endswith("/copy"))


class PlatformLinkAuditTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads((ROOT / "tests" / "fixtures" / "platform_links.json").read_text())

    def occurrence(self, key="one", **kwargs):
        defaults = dict(occurrence_id=f"synthetic:{key}", assignment_key=self.fixture["assignment_key"], surface="slide_text", location=f"slide:one/shape:{key}", destination=self.fixture["deck_reference"], revision=self.fixture["expected_revision"])
        return LinkOccurrence(**dict(defaults, **kwargs))

    def test_canonicalization_preserves_meaningful_modes_fragments_and_query(self):
        self.assertEqual(canonicalize_url("https://EXAMPLE.invalid:443/slides/Deck/copy?z=2&utm_source=a&a=1#slide=id.p2"), "https://example.invalid/slides/Deck/copy?a=1&z=2#slide=id.p2")
        self.assertNotEqual(canonicalize_url("https://example.invalid/deck/edit"), canonicalize_url("https://example.invalid/deck/copy"))
        self.assertEqual(canonicalize_url("#slide=id.question_one"), "#slide=id.question_one")

    def test_missing_incorrect_duplicate_stale_and_correct_occurrences(self):
        expected = tuple(self.occurrence(key) for key in ("correct", "missing", "incorrect", "duplicate", "stale"))
        observed = (expected[0], replace(expected[2], destination=self.fixture["stale_deck_reference"]), expected[3], replace(expected[3], occurrence_id="synthetic:duplicate-two"), replace(expected[4], revision="synthetic:r1"))
        audit = audit_link_occurrences(expected, observed)
        self.assertEqual({r.status for r in audit.occurrences}, {"correct", "missing", "incorrect", "duplicate", "stale"})
        self.assertEqual(len(audit.occurrences), 6)
        self.assertEqual(audit, audit_link_occurrences(tuple(reversed(expected)), tuple(reversed(observed))))
        self.assertEqual(audit.network_calls, 0)
        self.assertEqual({r.access_status for r in audit.occurrences}, {"not_tested_offline"})

    def test_every_surface_gets_an_occurrence_and_cross_slide_reuse_is_not_duplicate(self):
        expected = tuple(self.occurrence(surface, surface=surface, destination=self.fixture["internal_reference"] if surface == "internal_slide" else self.fixture["deck_reference"]) for surface in sorted(LINK_SURFACES))
        result = audit_link_occurrences(expected, expected)
        self.assertEqual(len(result.occurrences), len(LINK_SURFACES))
        self.assertEqual({row.status for row in result.occurrences}, {"correct"})

    def test_unsafe_urls_never_get_fetched_or_leak_into_findings(self):
        unsafe = ["javascript:alert(1)", "file:///private/example", "https://example.invalid/delete/item", "https://example.invalid/action?token=fixture", "https://127.0.0.1/item", "https://example.invalid/?next=https%3A%2F%2Fexample.invalid"]
        prohibited = "https://" + "answerableteaching.com" + "/manual"
        for url in unsafe + [prohibited]:
            with self.assertRaises(UnsafeLink):
                canonicalize_url(url)
            observed = self.occurrence(destination=url)
            result = audit_link_occurrences((self.occurrence(),), (observed,))
            self.assertEqual(result.occurrences[0].status, "unsafe")
            self.assertIsNone(result.occurrences[0].observed_destination)
            self.assertNotIn(url, json.dumps(asdict(result)))

    def test_unexpected_link_and_unknown_revision_are_not_passed(self):
        row = self.occurrence()
        self.assertEqual(audit_link_occurrences((), (row,)).occurrences[0].status, "stale")
        self.assertEqual(audit_link_occurrences((row,), (replace(row, revision="UNVERIFIED"),)).occurrences[0].status, "unverified")


if __name__ == "__main__":
    unittest.main()
