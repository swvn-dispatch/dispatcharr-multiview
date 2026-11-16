"""Tests for serialized plugin settings updates and normalization."""

import copy
import sys
import threading
import types
import unittest
from unittest.mock import patch

from src import config


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
        self.assertEqual(self.settings, result)


if __name__ == "__main__":
    unittest.main()
