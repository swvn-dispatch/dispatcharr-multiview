"""Tests for the v2 leadership and refresh scheduler."""

import threading
import sys
import types
from contextlib import nullcontext
import unittest
from unittest.mock import Mock, patch

import src


class ManagedLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.plugin = src.Plugin.__new__(src.Plugin)
        self.stop = threading.Event()
        self.context = {
            "start_service": Mock(),
            "get_settings": Mock(return_value={"epg_refresh_hours": 1}),
            "dispatch_task": Mock(),
            "dispatch_scheduled_task": Mock(),
            "dispatch_internal_task": Mock(),
            "data_dir": "/unused",
        }
        celery = types.ModuleType("celery")
        celery_result = types.ModuleType("celery.result")
        celery_result.AsyncResult = Mock()
        modules = patch.dict(sys.modules, {"celery": celery, "celery.result": celery_result})
        modules.start()
        self.addCleanup(modules.stop)
        refresh = patch.object(src, "_load_submodule", return_value=Mock())
        refresh.start()
        self.addCleanup(refresh.stop)
        migration = patch.object(self.plugin, "_migrate_storage")
        migration.start()
        self.addCleanup(migration.stop)

    def test_v2_leader_starts_host_managed_service(self):
        self.plugin.on_leader_acquired(self.context)
        self.context["start_service"].assert_called_once()
        self.assertEqual(self.context["start_service"].call_args.kwargs["name"], "multiview")

    def test_v2_construction_and_stop_do_not_start_or_stop_services(self):
        with (
            patch.object(src, "_server") as server,
        ):
            plugin = src.Plugin()
            plugin.stop({})
        server.assert_not_called()

    @patch.object(src, "_close_db_connections")
    def test_interval_changes_and_manual_only_mode(self, close_connections):
        settings = {"epg_refresh_hours": 1}
        self.context["get_settings"].side_effect = lambda: settings
        listener = Mock()

        def serve(stop_event, on_ready, on_tick):
            with patch.object(src.time, "monotonic", return_value=0):
                on_tick()
            with patch.object(src.time, "monotonic", return_value=3595):
                on_tick()
            self.context["dispatch_scheduled_task"].assert_not_called()
            with patch.object(src.time, "monotonic", return_value=3600):
                on_tick()
            self.context["dispatch_scheduled_task"].assert_called_once_with("generate_m3u", {})
            settings["epg_refresh_hours"] = 0
            with patch.object(src.time, "monotonic", return_value=3605):
                on_tick()
            with patch.object(src.time, "monotonic", return_value=99999):
                on_tick()
            self.context["dispatch_scheduled_task"].assert_called_once()

        listener.serve.side_effect = serve
        with patch.object(src, "_server") as module:
            module.return_value.MultiviewServer.return_value = listener
            self.plugin._run_managed_service(self.stop, self.context)
        self.assertEqual(close_connections.call_count, 5)

    @patch.object(src, "_close_db_connections")
    def test_dispatch_failure_retries_without_moving_deadline(self, close_connections):
        self.context["dispatch_scheduled_task"].side_effect = [RuntimeError("queue unavailable"), None]
        listener = Mock()

        def serve(stop_event, on_ready, on_tick):
            with patch.object(src.time, "monotonic", side_effect=[0, 3600, 3605]):
                on_tick()
                on_tick()
                on_tick()

        listener.serve.side_effect = serve
        with patch.object(src, "_server") as module, self.assertLogs(src.logger, level="ERROR"):
            module.return_value.MultiviewServer.return_value = listener
            self.plugin._run_managed_service(self.stop, self.context)
        self.assertEqual(self.context["dispatch_scheduled_task"].call_count, 2)

    @patch.object(src, "_close_db_connections")
    def test_ready_queues_architecture_specific_consent_aware_check(self, close_connections):
        listener = Mock()
        listener.serve.side_effect = lambda stop_event, on_ready, on_tick: on_ready()
        deps = Mock()
        deps.detect_arch.return_value = "linux-aarch64"
        with patch.object(src, "_server") as module, patch.object(self.plugin, "_deps", return_value=deps):
            module.return_value.MultiviewServer.return_value = listener
            self.plugin._run_managed_service(self.stop, self.context)
        self.context["dispatch_task"].assert_called_once_with("install_pyav_arm64", {"automatic": True})

    def test_automatic_install_action_uses_consent_checked_repair(self):
        deps = Mock()
        deps.maybe_auto_install.return_value = None
        with patch.object(self.plugin, "_deps", return_value=deps):
            result = self.plugin.run("install_pyav_arm64", {"automatic": True}, {})
        deps.maybe_auto_install.assert_called_once_with("linux-aarch64")
        deps.install_pyav.assert_not_called()
        self.assertEqual(result["status"], "success")


class StorageStartupTests(unittest.TestCase):
    def test_only_available_migrated_files_update_database_paths(self):
        plugin = src.Plugin.__new__(src.Plugin)
        refresh = types.SimpleNamespace(locked=lambda _: nullcontext())
        storage = types.SimpleNamespace(migrate_legacy_files=Mock(return_value={
            "files": {"multiview.m3u": "copied", "multiview_epg.xml": "missing"},
            "missing_backgrounds": ["missing.png"],
        }))
        m3u = Mock()
        epg = Mock()
        modules = {
            name: types.ModuleType(name)
            for name in ("django", "django.db", "apps", "apps.epg", "apps.epg.models", "apps.m3u", "apps.m3u.models")
        }
        modules["django.db"].transaction = types.SimpleNamespace(atomic=nullcontext)
        modules["apps.epg.models"].EPGSource = types.SimpleNamespace(objects=epg)
        modules["apps.m3u.models"].M3UAccount = types.SimpleNamespace(objects=m3u)
        with (
            patch.dict(sys.modules, modules),
            patch.object(src, "_load_submodule", side_effect=lambda name: {"refresh": refresh, "storage": storage}[name]),
            patch.object(src, "_config") as config,
            patch.object(src, "_close_db_connections"),
            self.assertLogs(src.logger, level="WARNING"),
        ):
            config.return_value.load_settings.return_value = {"multiview_order": ["stable"]}
            plugin._migrate_storage({"data_dir": "/durable"})
        m3u.filter.return_value.update.assert_called_once_with(file_path="/durable/multiview.m3u")
        epg.filter.assert_not_called()
        storage.migrate_legacy_files.assert_called_once_with(
            {"multiview_order": ["stable"]}, src._PLUGIN_DIR, "/durable",
        )


if __name__ == "__main__":
    unittest.main()
