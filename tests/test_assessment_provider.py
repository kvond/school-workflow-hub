"""Provider drafting: synthetic captures and a faked model; no network, no writes."""
import unittest
from unittest.mock import patch

from school_workflow_hub.adapters import assessment_provider as provider
from school_workflow_hub.adapters.hosted_agenda import ConnectionError
from tests.test_assessment_records import SOURCE, CAPTURE, packet

CONFIG = {"HUB_ANTHROPIC_API_KEY": "sk-ant-" + "x" * 40}


def request():
    base = packet()
    return {"file_id": base["source"]["file_id"], "class_key": base["class_key"],
            "section_id": base["section_id"], "section_name": base["section_name"],
            "assignment_key": base["assignment_key"], "stage": base["stage"],
            "stage_reference": base["stage_reference"], "identity": base["identity"],
            "manifest": base["manifest"], "ic_assignment_id": base["ic_assignment_id"],
            "comparison_status": base["comparison_status"]}


class ProviderTests(unittest.TestCase):
    def test_configuration_requires_a_key_shape_and_never_echoes_it(self):
        self.assertFalse(provider.configured({}))
        self.assertFalse(provider.configured({"HUB_ANTHROPIC_API_KEY": "wrong"}))
        self.assertTrue(provider.configured(CONFIG))
        pasted = {"HUB_ANTHROPIC_API_KEY": CONFIG["HUB_ANTHROPIC_API_KEY"] + "\n"}
        self.assertTrue(provider.configured(pasted))

    def test_draft_builds_a_validated_packet_with_python_arithmetic(self):
        base = packet()
        model_output = ('preamble {"responses": '
                        + __import__("json").dumps(base["responses"])
                        + ', "feedback": "Report text for the student."} trailer')
        with patch.object(provider, "_identity_configuration", lambda c: ("a", ["t"])), \
             patch.object(provider, "authorize", lambda *a, **k: None), \
             patch.object(provider.assessment_save, "capture",
                          lambda assertion, file_id, config: dict(CAPTURE, text=SOURCE)), \
             patch.object(provider, "_teacher_prompts", lambda: ("P1", "P2")), \
             patch.object(provider, "_call", lambda config, system, user: (
                 model_output, {"input_tokens": 11, "output_tokens": 7, "model": "m"})):
            result = provider.draft(None, request(), CONFIG)
        self.assertEqual(result["status"], "draft_ready")
        self.assertEqual(result["scores"]["content_notes"]["exact_value"], "20/3")
        self.assertEqual(result["packet"]["feedback"], "Report text for the student.")
        self.assertEqual(result["source"]["sha256"], "a" * 64)
        self.assertTrue(result["record_id"].startswith("asmt-"))
        self.assertEqual(result["external_writes"], 0)

    def test_a_fabricated_quote_from_the_model_fails_the_draft(self):
        base = packet()
        base["responses"][0]["response_excerpt"] = "words the student never wrote"
        bad = '{"responses": ' + __import__("json").dumps(base["responses"]) + ', "feedback": "x"}'
        with patch.object(provider, "_identity_configuration", lambda c: ("a", ["t"])), \
             patch.object(provider, "authorize", lambda *a, **k: None), \
             patch.object(provider.assessment_save, "capture",
                          lambda assertion, file_id, config: dict(CAPTURE, text=SOURCE)), \
             patch.object(provider, "_teacher_prompts", lambda: ("P1", "P2")), \
             patch.object(provider, "_call", lambda config, system, user: (bad, {})):
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
