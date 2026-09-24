"""The four-level critical-aspect scale and its exact-name join key."""
import unittest

from school_workflow_hub.workflows import understanding_scale as scale


class ScaleTests(unittest.TestCase):
    def test_new_levels_come_first_and_the_legacy_level_is_read_only_vocabulary(self):
        self.assertEqual(scale.LEVELS, ("Beginning", "Developing", "Proficient", "Extending"))
        self.assertEqual(scale.ALL_LEVELS, ("Beginning", "Developing", "Proficient", "Extending", "Almost there"))
        self.assertEqual([level for level, _ in scale.RUBRIC_KEY], list(scale.LEVELS))

    def test_aspect_key_ignores_only_the_deck_prefix_case_spacing_and_edges(self):
        self.assertEqual(scale.aspect_key("Your answer \u2014 Critical Aspect 2 \u00b7 Where  transferred energy goes"),
                         "where transferred energy goes")
        self.assertEqual(scale.aspect_key("Critical aspect: Producers as the entry point."),
                         "producers as the entry point")
        self.assertNotEqual(scale.aspect_key("Energy transfer"), scale.aspect_key("Where transferred energy goes"))
        self.assertEqual(scale.aspect_key(None), "")


if __name__ == "__main__":
    unittest.main()
