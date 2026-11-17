"""Tests for serialized plugin settings updates and normalization."""

import copy
import sys
import threading
import types
import unittest
from unittest.mock import patch

from src import config, server


class SettingsConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.lock = threading.Lock()
        self.settings = {"multiview_order": ["old"], "multiview_count": 1,
                         "multiview_old_name": "Existing"}
        self.first_read = threading.Event()
        self.release = threading.Event()
        self.pause_first = False
        test = self

        class Atomic:
            def __enter__(self):
                test.lock.acquire()

            def __exit__(self, *args):
                test.lock.release()

        class Manager:
            def select_for_update(self):
                return self

            def get(self, **kwargs):
                self.assert_locked()
                cfg = types.SimpleNamespace(settings=copy.deepcopy(test.settings))

                def save(update_fields):
                    test.assertEqual(update_fields, ["settings", "updated_at"])
                    test.settings = copy.deepcopy(cfg.settings)

                cfg.save = save
                if test.pause_first:
                    test.pause_first = False
                    test.first_read.set()
                    if not test.release.wait(3):
                        raise RuntimeError("test timed out")
                return cfg

            def assert_locked(self):
                test.assertTrue(test.lock.locked())

        modules = {
            "django": types.ModuleType("django"),
            "django.db": types.ModuleType("django.db"),
            "apps": types.ModuleType("apps"),
            "apps.plugins": types.ModuleType("apps.plugins"),
            "apps.plugins.models": types.ModuleType("apps.plugins.models"),
        }
        modules["django.db"].transaction = types.SimpleNamespace(atomic=Atomic)
        modules["apps.plugins.models"].PluginConfig = types.SimpleNamespace(objects=Manager())
        self.modules = patch.dict(sys.modules, modules)
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def test_overlapping_updates_preserve_both_changes(self):
        self.pause_first = True
        errors = []

        def run(updates):
            try:
                config.update_settings(updates)
            except Exception as exc:
                errors.append(exc)

        first = threading.Thread(target=run, args=({"first": 1},))
        second = threading.Thread(target=run, args=({"second": 2},))
        first.start()
        try:
            self.assertTrue(self.first_read.wait(2))
            second.start()
        finally:
            self.release.set()
            first.join(3)
            if second.ident is not None:
                second.join(3)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.settings["first"], 1)
        self.assertEqual(self.settings["second"], 2)
        self.assertEqual(self.settings["multiview_old_name"], "Existing")

    def test_legacy_slots_migrate_to_ordered_table_without_changing_identity(self):
        self.settings.update({
            "multiview_old_channel_count": 3,
            "multiview_old_channel_1": "11",
            "multiview_old_channel_2": "22",
            "multiview_old_channel_3": "11",
        })
        result = config.load_settings()
        self.assertEqual(self.settings["multiview_order"], ["old"])
        self.assertEqual([row["channel"] for row in self.settings["multiview_old_channels"]], ["11", "22", "11"])
        self.assertNotIn("multiview_old_channel_1", self.settings)
        self.assertEqual(result["multiview_old_channel_3"], "11")
        self.assertEqual(self.settings["multiview_pre_channel_table_backup"]["settings"]["multiview_old_channel_2"], "22")
        saved = copy.deepcopy(self.settings)
        config.load_settings()
        self.assertEqual(self.settings, saved)

    def test_dashboard_slot_updates_write_the_canonical_table(self):
        self.settings["multiview_old_channels"] = [{"channel": "1"}, {"channel": "2"}]
        config.update_settings({"multiview_old_channel_count": 3, "multiview_old_channel_3": "7"})
        self.assertEqual([row["channel"] for row in self.settings["multiview_old_channels"]], ["1", "2", "7"])
        self.assertNotIn("multiview_old_channel_3", self.settings)
        self.assertEqual(config.load_settings()["multiview_old_channel_count"], 3)

    def test_overlapping_dashboard_channel_edits_preserve_both_rows(self):
        self.settings["multiview_old_channels"] = [{"channel": "1"}, {"channel": "2"}]
        self.pause_first = True
        errors = []

        def update(value):
            try:
                config.update_settings(value)
            except Exception as exc:
                errors.append(exc)

        first = threading.Thread(target=update, args=({"multiview_old_channel_1": "10"},))
        second = threading.Thread(target=update, args=({"multiview_old_channel_2": "20"},))
        first.start()
        try:
            self.assertTrue(self.first_read.wait(2))
            second.start()
        finally:
            self.release.set()
            first.join(3)
            if second.ident is not None:
                second.join(3)
        self.assertEqual(errors, [])
        self.assertEqual([row["channel"] for row in self.settings["multiview_old_channels"]], ["10", "20"])

    def test_native_table_edit_updates_dashboard_projection(self):
        self.settings["multiview_old_channels"] = [{"channel": "22"}, {"channel": "11"}, {"channel": "33"}]
        result = config.load_settings()
        self.assertEqual(result["multiview_old_channel_count"], 3)
        self.assertEqual([result[f"multiview_old_channel_{n}"] for n in range(1, 4)], ["22", "11", "33"])

    def test_native_row_deletion_keeps_audio_on_the_same_remaining_row(self):
        self.settings.update({
            "multiview_old_channels": [{"channel": "11"}, {"channel": "22"}, {"channel": "33"}],
            "multiview_old_audio_source": "2",
        })
        config.load_settings()
        self.settings["multiview_old_channels"].pop(0)
        result = config.load_settings()
        self.assertEqual(result["multiview_old_audio_source"], "1")
        self.assertEqual(result["multiview_old_channel_2"], "33")

    def test_explicit_audio_change_wins_over_anchor(self):
        self.settings.update({
            "multiview_old_channels": [{"channel": "11"}, {"channel": "22"}, {"channel": "33"}],
            "multiview_old_audio_source": "2",
        })
        config.load_settings()
        self.settings["multiview_old_channels"].pop(0)
        self.settings["multiview_old_audio_source"] = "0"
        self.assertEqual(config.load_settings()["multiview_old_audio_source"], "0")

    def test_regex_keeps_its_independent_maximum(self):
        self.settings.update({"multiview_old_selector_type": "regex", "multiview_old_channel_count": 7,
                              "multiview_old_channels": [{"channel": "1"}, {"channel": "2"}]})
        result = config.load_settings()
        self.assertEqual(result["multiview_old_channel_count"], 7)
        self.assertEqual(result["multiview_old_channel_2"], "2")

    def test_switching_selection_modes_normalizes_audio_choice(self):
        self.settings.update({"multiview_old_channels": [{"channel": "1"}, {"channel": "2"}],
                              "multiview_old_audio_source": "1"})
        config.update_settings({"multiview_old_selector_type": "regex"})
        self.assertEqual(self.settings["multiview_old_audio_source"], "regex_first")
        config.update_settings({"multiview_old_selector_type": "classic"})
        self.assertEqual(self.settings["multiview_old_audio_source"], "0")

    def test_channel_count_update_out_of_range_is_rejected(self):
        self.settings["multiview_old_channels"] = [{"channel": "1"}, {"channel": "2"}]
        with self.assertRaises(ValueError):
            config.update_settings({"multiview_old_channel_count": 10})

    def test_server_does_not_fabricate_layouts_when_database_fails(self):
        with patch.object(config, "load_settings", side_effect=RuntimeError("database unavailable")):
            with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                server._settings()

    def test_order_updates_count_and_null_removes_key(self):
        config.update_settings({"multiview_order": ["old", "new"],
                                "multiview_old_name": None})
        self.assertEqual(self.settings["multiview_count"], 2)
        self.assertNotIn("multiview_old_name", self.settings)

    def test_normalization_preserves_existing_settings(self):
        del self.settings["multiview_order"]
        self.settings["multiview_1_name"] = "Legacy"
        result = config.load_settings()
        layout_id = result["multiview_order"][0]
        self.assertEqual(result[f"multiview_{layout_id}_name"], "Legacy")
        self.assertEqual(config.project_channel_rows(self.settings), result)


if __name__ == "__main__":
    unittest.main()
