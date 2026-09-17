import unittest

from school_workflow_hub.workflows import chat_handoff

PROMPTS = {"prompts": {"feedback": "Prompt 2 — feedback for students\nBody.",
                       "summary": "Prompt 3 — class summary\nBody."},
           "sha256": {"feedback": "abc123", "summary": "def456"},
           "version_label": "feedback-v2.1",
           "bundled_matches": {"feedback": True, "summary": True}}

CONTEXT = {"class_display_name": "Forensics A", "lesson_date": "2026-09-16",
           "assignment_title": "Cycle 3 deck", "stage": "final",
           "folder_reference": "TurnIn/Forensics/Cycle3"}

DESTINATION = {"account": "AIHS ChatGPT", "profile": "aihs"}


def build(aliases=("Maria G", "Devon P"), task="feedback", **over):
    context = dict(CONTEXT, **over.pop("context", {}))
    return chat_handoff.build_handoff(
        task, over.pop("prompts", PROMPTS), context,
        {"index": 1, "total": 2, "aliases": list(aliases)}, DESTINATION)


class BuildTests(unittest.TestCase):
    def test_block_carries_context_aliases_prompt_and_return_fields(self):
        result = build()
        self.assertIn("Forensics A", result["block"])
        self.assertIn("Maria G, Devon P", result["block"])
        self.assertIn("Prompt 2 — feedback for students", result["block"])
        self.assertIn("learner_alias", result["block"])
        self.assertIn("feedback-v2.1", result["block"])
        self.assertEqual(result["destination"]["profile"], "aihs")
        self.assertEqual(result["external_writes"], 0)
        self.assertEqual(result["live_model_calls"], 0)

    def test_a_full_name_is_refused(self):
        for bad in (("Maria Gonzalez",), ("Maria G Gonzalez",), ("  ",)):
            with self.assertRaises(ValueError):
                build(bad)

    def test_first_name_with_or_without_period_is_accepted(self):
        self.assertIn("Devon P.", build(("Devon P.",))["block"])

    def test_duplicate_alias_and_empty_batch_refused(self):
        for bad in (("Maria G", "Maria G"), ()):
            with self.assertRaises(ValueError):
                build(bad)

    def test_prompt_out_of_sync_refuses_to_build(self):
        stale = dict(PROMPTS, bundled_matches={"feedback": False, "summary": True})
        with self.assertRaises(ValueError):
            build(prompts=stale)

    def test_feedback_batches_at_twenty_and_summary_does_not(self):
        twenty_one = tuple("Student{} X".format(n) for n in range(21))
        with self.assertRaises(ValueError):
            build(twenty_one)
        self.assertEqual(len(build(twenty_one, task="summary")["batch"]["aliases"]), 21)

    def test_unknown_task_stage_or_missing_context_refused(self):
        with self.assertRaises(ValueError):
            build(task="printing")
        with self.assertRaises(ValueError):
            build(context={"stage": "midway"})
        with self.assertRaises(ValueError):
            build(context={"folder_reference": ""})


class ReturnTests(unittest.TestCase):
    def record(self, alias, **over):
        base = {"learner_alias": alias, "prompt_version": "feedback-v2.1",
                "understanding_level": "Almost there", "evidence_quote": "cells divide"}
        base.update(over)
        return base

    def test_partial_return_is_accepted_and_names_who_is_missing(self):
        handoff = build()
        result = chat_handoff.validate_return(handoff, [self.record("Maria G")])
        self.assertEqual(result["returned"], 1)
        self.assertEqual(result["expected"], 2)
        self.assertEqual(result["still_waiting"], ("Devon P",))
        self.assertFalse(result["complete"])

    def test_full_return_completes(self):
        handoff = build()
        result = chat_handoff.validate_return(
            handoff, [self.record("Maria G"), self.record("Devon P")])
        self.assertTrue(result["complete"])
        self.assertEqual(result["still_waiting"], ())

    def test_stranger_duplicate_stale_prompt_bad_level_and_extra_field_refused(self):
        handoff = build()
        for bad in ([self.record("Someone Else")],
                    [self.record("Maria G"), self.record("Maria G")],
                    [self.record("Maria G", prompt_version="feedback-v2.0")],
                    [self.record("Maria G", understanding_level="Great")],
                    [dict(self.record("Maria G"), student_name="Maria Gonzalez")]):
            with self.assertRaises(ValueError):
                chat_handoff.validate_return(handoff, bad)

    def test_a_foreign_handoff_is_refused(self):
        with self.assertRaises(ValueError):
            chat_handoff.validate_return({"schema_version": "other/v1"}, [])



RESEARCH_PROMPTS = {
    "prompts": {"feedback": "Prompt 2 — feedback.", "summary": "Prompt 3 — summary.",
                "research": "Prompt 4 — research coding."},
    "sha256": {"feedback": "abc123", "summary": "def456", "research": "ghi789"},
    "version_label": "feedback-v2.1",
    "bundled_matches": {"feedback": True, "summary": True, "research": True}}


class SelfContainedBlockTest(unittest.TestCase):
    """The chat account cannot reach Drive, the Agenda sheet or the dashboard.

    A block that points it at one of those is refused outright rather than
    worked, so no standing rule may carry an address. The rules may name a
    check in order to say the assistant does not own it; they may not link out.
    """

    ADDRESSES = ("http", "docs.google.com", "drive.google.com", "infinitecampus",
                 "run.app", "@", ".com", ".org", ".edu")

    def _all_rule_lines(self):
        lines = list(chat_handoff.SELF_CONTAINED)
        for task_lines in chat_handoff.TASK_RULES.values():
            lines.extend(task_lines)
        return lines

    def test_every_task_has_rules(self):
        self.assertEqual(set(chat_handoff.TASK_RULES), set(chat_handoff.TASK_LIMITS))

    def test_no_standing_rule_carries_an_address(self):
        for line in self._all_rule_lines():
            for address in self.ADDRESSES:
                self.assertNotIn(address, line.lower(),
                                 "a standing rule points outside the block: " + line)

    def test_block_states_it_is_self_contained_for_every_task(self):
        for task in chat_handoff.TASK_LIMITS:
            handoff = chat_handoff.build_handoff(
                task, RESEARCH_PROMPTS, CONTEXT,
                {"index": 1, "total": 1, "aliases": ["Ana R"]}, DESTINATION)
            self.assertTrue(handoff["self_contained"])
            self.assertIn("HOW TO USE THIS BLOCK", handoff["block"])
            self.assertIn("nothing here requires them.", handoff["block"])
            for line in chat_handoff.TASK_RULES[task]:
                self.assertIn(line, handoff["block"])

    def test_rules_are_task_specific_not_one_shared_paragraph(self):
        feedback = chat_handoff.TASK_RULES["feedback"]
        summary = chat_handoff.TASK_RULES["summary"]
        research = chat_handoff.TASK_RULES["research"]
        self.assertNotEqual(feedback, summary)
        self.assertNotEqual(summary, research)
        self.assertIn("not batched", " ".join(summary))
        self.assertIn("Collection is disabled", " ".join(research))

if __name__ == "__main__":
    unittest.main()
