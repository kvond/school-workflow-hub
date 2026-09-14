import unittest
from scripts.hub_status import inventory


class CapabilityTests(unittest.TestCase):
    def test_inventory_links_resolve_and_live_readiness_is_explicit(self):
        data = inventory()
        self.assertEqual(data["schema_version"], "hub-capabilities/v1")
        items = {item["id"]: item for item in data["capabilities"]}
        self.assertFalse(items["agenda_reader"]["live_verified"])
        self.assertIn("schoology_ic", items)
        self.assertFalse(data["automatic_school_writes"])
        self.assertTrue(all(item["remaining"] for item in items.values()))
