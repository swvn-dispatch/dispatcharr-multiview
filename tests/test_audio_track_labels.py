"""Regression coverage for generated multiview audio-track labels."""

from types import SimpleNamespace
import unittest

from src import config, server
from src.audio_metadata import generated_audio_metadata_args


class AudioTrackLabelFieldTests(unittest.TestCase):
    def test_audio_track_labels_default_to_enabled(self):
        fields = config.build_plugin_fields({})
        field = next(field for field in fields if field["id"] == "audio_track_labels")

        self.assertEqual(field["default"], "enabled")
        self.assertEqual(
            [option["value"] for option in field["options"]],
            ["enabled", "disabled"],
        )


class AudioTrackLabelWorkerConfigTests(unittest.TestCase):
    def setUp(self):
        self.server = server.MultiviewServer("127.0.0.1", 9292)
        self.tiles = [
            {"id": 1, "name": "CA | TSN One"},
            {"id": 2, "name": "CA | TSN Two"},
        ]

    def test_missing_saved_setting_keeps_generated_labels_enabled(self):
        cfg = self.server._worker_config(self.tiles, "auto", "all", {})

        self.assertEqual(cfg["audio_track_labels"], "enabled")

    def test_disabled_setting_is_passed_to_worker(self):
        cfg = self.server._worker_config(
            self.tiles, "auto", "all", {"audio_track_labels": "disabled"},
        )

        self.assertEqual(cfg["audio_track_labels"], "disabled")


class AudioTrackLabelMetadataTests(unittest.TestCase):
    def setUp(self):
        self.channels = [
            SimpleNamespace(name="TSN One", lang="ts1"),
            SimpleNamespace(name="TSN Two", lang="ts2"),
        ]

    def test_enabled_generates_title_and_language_for_each_track(self):
        self.assertEqual(
            generated_audio_metadata_args(self.channels),
            [
                "-metadata:s:a:0", "title=TSN One",
                "-metadata:s:a:0", "language=ts1",
                "-metadata:s:a:1", "title=TSN Two",
                "-metadata:s:a:1", "language=ts2",
            ],
        )

    def test_disabled_omits_generated_metadata_for_every_track(self):
        self.assertEqual(generated_audio_metadata_args(self.channels, enabled=False), [])


if __name__ == "__main__":
    unittest.main()
