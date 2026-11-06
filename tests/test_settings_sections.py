import json
import unittest
from unittest.mock import patch

from src import config
from src.dash import api


class SettingsSectionTests(unittest.TestCase):
    def test_sections_follow_stable_layout_order(self):
        settings = {
            "multiview_order": ["abcdef01", "01234567"],
            "multiview_abcdef01_selector_type": "regex",
            "multiview_01234567_epg_source_mode": "forward",
            "video_encoder": "h264_vaapi",
        }
        fields = config.build_plugin_fields(settings)
        sections = [field for field in fields if field["type"] == "section"]
        self.assertEqual([field["id"] for field in sections], [
            "_global_settings_header", "_video_settings_header",
            "_section_layout_abcdef01", "_section_layout_01234567",
        ])
        ids = [field["id"] for field in fields]
        self.assertEqual(len(ids), len(set(ids)))
        for field in fields:
            fid = field["id"]
            if fid.startswith("multiview_abcdef01_") and field.get("label"):
                self.assertEqual(field["section"], "_section_layout_abcdef01")
            if fid.startswith("multiview_01234567_") and field.get("label"):
                self.assertEqual(field["section"], "_section_layout_01234567")
        self.assertEqual(ids.count("multiview_abcdef01_regex_pattern"), 1)
        self.assertIn("multiview_01234567_epg_forward_channel", ids)
        self.assertEqual(next(f for f in fields if f["id"] == "render_device")["section"], "_video_settings_header")
        self.assertNotIn("section", config._GLOBAL_SETTINGS_FIELDS[0])
        self.assertNotIn("section", config._VIDEO_OUTPUT_FIELDS[0])

    def test_conditional_layout_keys_remain_declared_when_not_visible(self):
        fields = config.build_plugin_fields({"multiview_order": ["abcdef01"]})
        by_id = {field["id"]: field for field in fields}
        self.assertEqual(by_id["multiview_abcdef01_regex_pattern"]["type"], "info")
        self.assertEqual(by_id["multiview_abcdef01_epg_forward_channel"]["type"], "info")

    def test_existing_stable_ids_are_recovered_when_order_was_filtered(self):
        settings, changed = config.ensure_layout_order({
            "multiview_abcdef01_name": "First",
            "multiview_01234567_name": "Second",
            "multiview_count": 2,
        })
        self.assertTrue(changed)
        self.assertEqual(settings["multiview_order"], ["abcdef01", "01234567"])

    def test_dashboard_does_not_expose_section_markers(self):
        settings = {"multiview_order": ["abcdef01"], "multiview_custom_layouts_order": ["style"]}
        with patch.object(api, "_verify_token", return_value=True), \
             patch.object(api, "_get_settings", return_value=settings), \
             patch.object(api, "_get_config_mod", return_value=config):
            response = api.handle_fields({"REQUEST_METHOD": "GET"}, lambda *_: None)
        result = json.loads(b"".join(response))
        self.assertIn("output_resolution", [field["id"] for field in result["global"]])
        self.assertNotIn("multiview_custom_layouts_order", [field["id"] for field in result["global"]])
        self.assertTrue(result["layouts"][0]["fields"])
        self.assertTrue(all(field["type"] != "section" for field in result["global"] + result["layouts"][0]["fields"]))

    def test_hidden_settings_are_declared_for_native_save(self):
        settings = {
            "multiview_order": ["abcdef01"],
            "multiview_abcdef01_selector_type": "regex",
            "multiview_abcdef01_channel_1": "42",
            "multiview_custom_layouts": {"x": {"name": "Example"}},
            "pyav_consent_linux-x86_64": True,
        }
        fields = config.build_plugin_fields(settings)
        by_id = {field["id"]: field for field in fields}
        for key in settings:
            self.assertIn(key, by_id)
        for key in ("multiview_order", "multiview_abcdef01_channel_1", "multiview_custom_layouts"):
            self.assertEqual(by_id[key], {"id": key, "type": "info"})


if __name__ == "__main__":
    unittest.main()
