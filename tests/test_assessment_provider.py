"""Provider drafting: synthetic captures and a faked model; no network, no writes."""
import unittest
from unittest.mock import patch

from school_workflow_hub.adapters import assessment_provider as provider
from school_workflow_hub.adapters import firestore_state
from school_workflow_hub.adapters.hosted_agenda import ConnectionError
from tests.test_assessment_records import SOURCE, CAPTURE, packet
from tests.test_cloud_state import MemoryCAS

CONFIG = {"HUB_ANTHROPIC_API_KEY": "sk-ant-" + "x" * 40}


class drafting:
    """Common patches for a draft run against an in-memory store."""
    def __init__(self, model_output, prior=None):
        self.calls = 0
        self.port = MemoryCAS()
        def call(config, system, user):
            self.calls += 1
            return model_output, {"input_tokens": 11, "output_tokens": 7, "model": "m"}
        self.patches = [
            patch.object(provider, "_identity_configuration", lambda c: ("a", ["t"])),
            patch.object(provider, "authorize", lambda *a, **k: None),
            patch.object(provider.assessment_save, "capture",
                         lambda assertion, file_id, config: dict(CAPTURE, text=SOURCE)),
            patch.object(provider, "_teacher_prompts",
                         lambda assertion, config: ("P1", "P2", {"version_label": "v"})),
            patch.object(provider.assessment_target, "read_target", lambda *a: prior),
            patch.object(firestore_state, "configured_store", lambda config=None: self.port),
            patch.object(provider, "_call", call),
        ]
    def __enter__(self):
        for p in self.patches: p.start()
        return self
    def __exit__(self, *a):
        for p in self.patches: p.stop()


def request():
    base = packet()
    return {"file_id": base["source"]["file_id"], "class_key": base["class_key"],
            "section_id": base["section_id"], "section_name": base["section_name"],
            "assignment_key": base["assignment_key"], "stage": base["stage"],
            "stage_reference": base["stage_reference"], "identity": base["identity"]}


class ProviderTests(unittest.TestCase):
    def test_four_level_draft_keeps_explicit_rubric_on_recovery_and_workbook_rows(self):
        import json
        base = packet()
        for response in base["responses"]:
            response.update(rubric_version="critical-aspect-4/v1",
                            critical_aspect="Fictional aspect", explanation_level="")
            if response["judgment"] in {"blank", "printed_copy"}:
                response["conceptual_level"] = "Evidence unavailable"
        with drafting(json.dumps({"responses": base["responses"], "feedback": "Your answer explains the idea."})):
            result = provider.draft(None, request(), CONFIG)
            cached = provider.draft(None, request(), CONFIG)
        self.assertTrue(cached["cached"])
        from school_workflow_hub.workflows import assessment_records as ar
        saved = result["packet"]
        rows = ar.build_rows(saved, ar.completion(saved), CAPTURE, now="2026-09-18T15:05:00+00:00")
        self.assertTrue(all(a["rubric_version"] == "critical-aspect-4/v1" for a in rows["answers"]))
        self.assertEqual(cached["packet"], saved)

    def test_configuration_requires_a_key_shape_and_never_echoes_it(self):
        self.assertFalse(provider.configured({}))
        self.assertFalse(provider.configured({"HUB_ANTHROPIC_API_KEY": "wrong"}))
        self.assertTrue(provider.configured(CONFIG))
        pasted = {"HUB_ANTHROPIC_API_KEY": CONFIG["HUB_ANTHROPIC_API_KEY"] + "\n"}
        self.assertTrue(provider.configured(pasted))

    def test_draft_builds_a_validated_packet_and_persists_it(self):
        base = packet()
        model_output = ('preamble {"responses": '
                        + __import__("json").dumps(base["responses"])
                        + ', "feedback": "Report text for the student."} trailer')
        with drafting(model_output) as run:
            result = provider.draft(None, request(), CONFIG)
            again = provider.draft(None, request(), CONFIG)
            recovered = provider.stored_draft_for(None, request()["file_id"], CONFIG,
                                                  target=provider._draft_target(request()))
        self.assertEqual(result["status"], "draft_ready")
        self.assertFalse(result["cached"])
        self.assertEqual(result["scores"]["content_notes"]["exact_value"], "20/3")
        self.assertEqual(result["packet"]["feedback"], "Report text for the student.")
        self.assertEqual(result["source"]["sha256"], "a" * 64)
        self.assertTrue(result["record_id"].startswith("asmt-"))
        self.assertEqual(result["external_writes"], 0)
        # Identical content never bills twice, and the paid draft survives a
        # page refresh through the stored-draft read.
        self.assertEqual(run.calls, 1)
        self.assertTrue(again["cached"])
        self.assertEqual(recovered["status"], "draft_ready")
        self.assertEqual(recovered["packet"]["feedback"], result["packet"]["feedback"])

    def test_notes_and_drafts_share_revision_but_keep_independent_drafts_and_claims(self):
        import json
        base = packet()
        output = json.dumps({"responses": base["responses"], "feedback": "Synthetic report."})
        notes, final = dict(request(), stage="initial"), request()
        with drafting(output) as run:
            first = provider.draft(None, notes, CONFIG)
            second = provider.draft(None, final, CONFIG)
            for ask, expected in ((notes, first), (final, second)):
                cached = provider.draft(None, ask, CONFIG)
                recovered = provider.stored_draft_for(None, ask["file_id"], CONFIG,
                                                      target=provider._draft_target(ask))
                self.assertEqual(cached["packet"], expected["packet"])
                self.assertEqual(recovered["packet"], expected["packet"])
            self.assertEqual(run.calls, 2)
        self.assertNotEqual(first["record_id"], second["record_id"])

    def test_legacy_notes_cache_is_preserved_while_final_stage_gets_its_own_cache(self):
        import json
        from school_workflow_hub.core.cloud_state import CloudState
        base = packet()
        output = json.dumps({"responses": base["responses"], "feedback": "Synthetic report."})
        notes, final = dict(request(), stage="initial"), request()
        with drafting(output) as run:
            store = CloudState(run.port)
            legacy = {"packet": packet(stage="initial"), "drafted_at": "2026-09-01T00:00:00Z"}
            provider._store_draft(store, notes["file_id"], CAPTURE["sha256"], legacy)
            result = provider.draft(None, notes, CONFIG)
            self.assertTrue(result["cached"])
            self.assertEqual(run.calls, 0)
            provider.draft(None, final, CONFIG)
            self.assertEqual(run.calls, 1)
            self.assertEqual(provider._stored_draft(store, notes["file_id"], CAPTURE["sha256"]), legacy)
            self.assertEqual(provider.stored_draft_for(None, notes["file_id"], CONFIG)["packet"], legacy["packet"])

    def test_recovery_route_passes_exact_target_and_rejects_partial_target(self):
        from school_workflow_hub.web import application
        query = "draft_for=" + request()["file_id"] + "&class_key=biology&assignment_key=BIO-C02-F01&section_id=26716&stage=initial"
        statuses = []
        with patch.object(provider, "stored_draft_for", return_value={}) as read:
            application({"PATH_INFO": "/api/assessment-run", "REQUEST_METHOD": "GET", "QUERY_STRING": query},
                        lambda status, headers: statuses.append(status))
            self.assertEqual(read.call_args.kwargs["target"], provider._draft_target(dict(request(), stage="initial")))
            application({"PATH_INFO": "/api/assessment-run", "REQUEST_METHOD": "GET", "QUERY_STRING": query.rsplit("&", 1)[0]},
                        lambda status, headers: statuses.append(status))
            self.assertEqual(read.call_count, 1)
        self.assertEqual(statuses, ["200 OK", "400 Bad Request"])

    def test_already_assessed_unchanged_content_never_calls_the_model(self):
        prior = {"status": "assessed", "source_sha256": "a" * 64,
                 "assessment_stage": "final", "record_id": "asmt-x",
                 "checked_at": "2026-09-18T15:05:00+00:00"}
        with drafting('{"responses": [], "feedback": "x"}', prior=prior) as run:
            result = provider.draft(None, request(), CONFIG)
        self.assertEqual(result["status"], "already_assessed")
        self.assertEqual(run.calls, 0)

    def test_confirmed_name_gets_a_server_derived_learner_key(self):
        base = packet()
        model_output = ('{"responses": ' + __import__("json").dumps(base["responses"])
                        + ', "feedback": "Report."}')
        ask = request()
        ask["identity"] = dict(ask["identity"], student_id="", learner_key="")
        with drafting(model_output):
            result = provider.draft(None, ask, CONFIG)
        identity = result["packet"]["identity"]
        self.assertTrue(identity["learner_key"].startswith("roster:"))
        self.assertIn("not an IC-verified number", identity["identity_basis"])

    def test_a_fabricated_quote_from_the_model_fails_the_draft(self):
        base = packet()
        base["responses"][0]["response_excerpt"] = "words the student never wrote"
        bad = '{"responses": ' + __import__("json").dumps(base["responses"]) + ', "feedback": "x"}'
        with drafting(bad):
            with self.assertRaises(ValueError) as caught:
                provider.draft(None, request(), CONFIG)
        self.assertEqual(str(caught.exception), "assessment_evidence_not_in_source")

    def test_unconfigured_provider_is_a_fixed_dependency_code(self):
        with patch.object(provider, "_identity_configuration", lambda c: ("a", ["t"])), \
             patch.object(provider, "authorize", lambda *a, **k: None):
            with self.assertRaises(ConnectionError) as caught:
                provider.draft(None, request(), {})
        self.assertEqual(str(caught.exception), "provider_not_configured")

    def test_garbage_model_output_never_leaks_into_the_error(self):
        with self.assertRaises(ConnectionError) as caught:
            provider._extract_json("the model rambled with no json")
        self.assertEqual(str(caught.exception), "provider_draft_invalid")


if __name__ == "__main__":
    unittest.main()
