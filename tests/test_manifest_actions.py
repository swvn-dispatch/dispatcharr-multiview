"""Tests for action metadata shared by the plugin and its manifest."""

import json
from pathlib import Path
import unittest

import src


class ManifestActionTests(unittest.TestCase):
    def test_manifest_actions_are_plugin_actions(self):
        manifest_path = Path(__file__).resolve().parents[1] / "src" / "plugin.json"
        manifest = json.loads(manifest_path.read_text())
        self.assertEqual(src.Plugin.actions, manifest["actions"])
        self.assertEqual(
            {action["id"] for action in manifest["actions"]},
            {"generate_m3u", "reload_active_multiviews", "install_pyav_amd64", "install_pyav_arm64"},
        )
        self.assertEqual(len(manifest["actions"]), 4)


if __name__ == "__main__":
    unittest.main()
