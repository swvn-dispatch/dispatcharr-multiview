"""Native and dashboard views of per-layout channel selection."""

import unittest
from unittest.mock import patch

from src import config


class ChannelTableTests(unittest.TestCase):
    def setUp(self):
        self.settings = {
            "multiview_order": ["abcd1234"],
            "multiview_abcd1234_channels": [{"channel": "10"}, {"channel": "20"}],
        }
        self.options = [{"value": "_none", "label": "Select a channel"}, {"value": "10", "label": "Ten"}]

    def test_native_classic_uses_table_not_count_or_slots(self):
        with patch.object(config, "_build_channel_options", return_value=self.options):
            fields = config.build_plugin_fields(config.project_channel_rows(self.settings))
        by_id = {field["id"]: field for field in fields}
        table = by_id["multiview_abcd1234_channels"]
        self.assertEqual(table["type"], "table")
        self.assertEqual(table["columns"][0]["options"], self.options + [{"value": "20", "label": "Unavailable channel (20)"}])
        self.assertNotIn("multiview_abcd1234_channel_count", by_id)
        self.assertNotIn("multiview_abcd1234_channel_1", by_id)

    def test_dashboard_keeps_slot_controls(self):
        with patch.object(config, "_build_channel_options", return_value=self.options):
            fields = config.build_plugin_fields(config.project_channel_rows(self.settings), dashboard=True)
        ids = {field["id"] for field in fields}
        self.assertIn("multiview_abcd1234_channel_count", ids)
        self.assertIn("multiview_abcd1234_channel_1", ids)
        self.assertNotIn("multiview_abcd1234_channels", ids)

    def test_regex_retains_maximum_channel_field(self):
        self.settings["multiview_abcd1234_selector_type"] = "regex"
        self.settings["multiview_abcd1234_channel_count"] = 5
        fields = config.build_plugin_fields(config.project_channel_rows(self.settings))
        ids = {field["id"] for field in fields}
        self.assertIn("multiview_abcd1234_channel_count", ids)
        self.assertNotIn("multiview_abcd1234_channels", ids)


if __name__ == "__main__":
    unittest.main()
