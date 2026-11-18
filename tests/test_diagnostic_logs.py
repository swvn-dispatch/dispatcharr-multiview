"""Per-tile diagnostics are opt-in and propagate to worker channels."""

import unittest

from src import config, server


class DiagnosticLogTests(unittest.TestCase):
    def test_setting_defaults_to_disabled(self):
        field = next(f for f in config.build_plugin_fields({}) if f["id"] == "diagnostic_logs")
        self.assertEqual(field["default"], "disabled")

    def test_worker_and_tiles_receive_setting(self):
        instance = server.MultiviewServer("127.0.0.1", 9292, "/unused")
        tiles = [{"id": 1, "name": "One"}, {"id": 2, "name": "Two"}]
        for settings, expected in (({}, False), ({"diagnostic_logs": "disabled"}, False),
                                   ({"diagnostic_logs": "enabled"}, True)):
            with self.subTest(settings=settings):
                cfg = instance._worker_config(tiles, "auto", "all", settings)
                self.assertIs(cfg["diagnostic_logs"], expected)
                self.assertTrue(all(tile["diagnostic_logs"] is expected for tile in cfg["tiles"]))


if __name__ == "__main__":
    unittest.main()
