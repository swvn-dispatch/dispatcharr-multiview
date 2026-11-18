"""Tests for action metadata shared by the plugin and its manifest."""

import json
from pathlib import Path
import unittest

import src


class ManifestActionTests(unittest.TestCase):
    def test_manifest_declares_v2_and_required_managed_capabilities(self):
        manifest_path = Path(__file__).resolve().parents[1] / "src" / "plugin.json"
        manifest = json.loads(manifest_path.read_text())
        self.assertEqual(manifest["manifest_version"], 2)
        self.assertEqual(manifest["min_dispatcharr_version"], "v0.34.0")
        self.assertTrue({"background_tasks", "persistent_service", "network_listener"}.issubset(manifest["capabilities"]))

    def test_manifest_actions_are_plugin_actions(self):
        manifest_path = Path(__file__).resolve().parents[1] / "src" / "plugin.json"
        manifest = json.loads(manifest_path.read_text())
        self.assertEqual(src.Plugin.actions, manifest["actions"])
        self.assertEqual(
            {action["id"] for action in manifest["actions"]},
            {"refresh_ui", "generate_m3u", "reload_active_multiviews", "install_pyav", "sample_diagnostics"},
        )
        self.assertEqual(len(manifest["actions"]), 5)
        self.assertEqual([action["id"] for action in manifest["actions"]], [
            "refresh_ui", "generate_m3u", "reload_active_multiviews", "install_pyav", "sample_diagnostics",
        ])
        actions = {action["id"]: action for action in manifest["actions"]}
        self.assertEqual(actions["refresh_ui"]["label"], "Refresh Settings")
        self.assertEqual(actions["refresh_ui"]["button_label"], "Refresh")
        self.assertEqual(actions["generate_m3u"]["button_label"], "Regenerate")
        self.assertEqual(actions["reload_active_multiviews"]["button_label"], "Reload Active")
        self.assertTrue(actions["sample_diagnostics"]["async"])
        self.assertEqual(actions["sample_diagnostics"]["button_label"], "Gather Diagnostics")
        self.assertEqual(actions["refresh_ui"]["button_color"], "blue")
        self.assertEqual(actions["sample_diagnostics"]["button_color"], "orange")
        self.assertEqual(actions["sample_diagnostics"]["button_variant"], "outline")
        self.assertEqual(actions["install_pyav"]["button_color"], "red")
        self.assertEqual(actions["generate_m3u"]["button_color"], "orange")
        self.assertEqual(actions["reload_active_multiviews"]["button_color"], "orange")

    def test_refresh_ui_action_requests_ui_refresh_only(self):
        result = src.Plugin().run("refresh_ui", {}, {})
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["_dispatcharr"], {"refresh": "ui"})


if __name__ == "__main__":
    unittest.main()
