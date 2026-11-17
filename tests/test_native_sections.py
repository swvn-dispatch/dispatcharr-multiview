"""Native settings use Dispatcharr sections without changing dashboard fields."""

import unittest
from unittest.mock import patch

from src import config


class NativeSectionsTests(unittest.TestCase):
    def test_sections_group_every_field_and_collapse_layouts(self):
        settings = {"multiview_order": ["12345678", "abcdef01"]}
        warning = {"id": "_warn_test", "type": "info", "label": "Test"}
        warnings = [{"id": "_warnings_header", "type": "info", "label": "Warnings"}, warning]
        with patch.object(config, "_build_warnings_fields", return_value=warnings):
            fields = config.build_plugin_fields(settings)
        sections = [field for field in fields if field["type"] == "section"]
        self.assertEqual([s["label"] for s in sections], ["Alerts", "Global Settings", "Video Settings", "Layout 1", "Layout 2"])
        self.assertEqual([s["collapsed"] for s in sections], [False, False, True, True, True])
        ids = {s["id"] for s in sections}
        for field in fields:
            if field["type"] != "section":
                self.assertIn(field.get("section"), ids, field["id"])
        by_id = {field["id"]: field for field in fields}
        self.assertEqual(by_id["_warn_test"]["section"], "_warnings_header")
        self.assertEqual(by_id["multiview_12345678_name"]["section"], "_multiview_12345678_header")
        self.assertEqual(by_id["multiview_abcdef01_name"]["section"], "_multiview_abcdef01_header")

    def test_no_empty_alerts_section_and_dashboard_unchanged(self):
        with patch.object(config, "_build_warnings_fields", return_value=[]):
            native = config.build_plugin_fields({})
            dashboard = config.build_plugin_fields({}, dashboard=True)
        self.assertNotIn("_warnings_header", [field["id"] for field in native])
        self.assertFalse(any(field["type"] == "section" for field in dashboard))


if __name__ == "__main__":
    unittest.main()
