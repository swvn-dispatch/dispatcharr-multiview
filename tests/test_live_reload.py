"""Regression coverage for automatic active-multiview reloads."""

import unittest
from io import BytesIO
from unittest.mock import Mock, patch

from src import Plugin, config, server


class LiveReloadFieldTests(unittest.TestCase):
    def test_live_reload_defaults_to_manual_with_supported_intervals(self):
        fields = config.build_plugin_fields({})
        field = next(field for field in fields if field["id"] == "live_reload_interval")

        self.assertEqual(field["default"], "manual")
        self.assertEqual(
            [option["value"] for option in field["options"]],
            ["manual", "15", "30", "60", "120", "300"],
        )

    def test_child_streams_default_to_live_edge_with_independent_tile_hold(self):
        fields = config.build_plugin_fields({})
        buffer = next(field for field in fields if field["id"] == "child_buffer_seconds")
        hold = next(field for field in fields if field["id"] == "tile_hold_seconds")

        self.assertEqual(buffer["default"], 0)
        self.assertEqual(hold["default"], 15)
        self.assertEqual(server.child_buffer_seconds({}), 0)
        self.assertEqual(server.tile_hold_seconds({}), 15)
        self.assertEqual(server.child_buffer_seconds({"child_buffer_seconds": 99}), 30)
        self.assertEqual(server.tile_hold_seconds({"tile_hold_seconds": 99}), 60)

    def test_worker_config_passes_child_buffer_to_each_tile(self):
        cfg = server.MultiviewServer("127.0.0.1", 9292)._worker_config(
            [{"id": 1, "name": "One", "logo": None}, {"id": 2, "name": "Two", "logo": None}],
            "auto", "0", {"child_buffer_seconds": 0, "tile_hold_seconds": 15},
        )

        self.assertEqual(
            [tile["url"] for tile in cfg["tiles"]],
            [
                "http://127.0.0.1:9292/internal/realsrc/1?buffer_seconds=0",
                "http://127.0.0.1:9292/internal/realsrc/2?buffer_seconds=0",
            ],
        )
        self.assertEqual([tile["tile_hold_secs"] for tile in cfg["tiles"]], [15, 15])


class ActiveWorkerReloadTests(unittest.TestCase):
    def setUp(self):
        self.server = server.MultiviewServer("127.0.0.1", 9292)
        self.proc = Mock()
        self.server._active_procs[self.proc] = {"n": "layout", "cfg": {"tiles": [{"url": "old"}]}}

    def test_manual_interval_has_no_seconds_value(self):
        self.assertIsNone(server._live_reload_interval_secs({"live_reload_interval": "manual"}))
        self.assertEqual(server._live_reload_interval_secs({"live_reload_interval": "15"}), 15)

    def test_unchanged_worker_is_not_reloaded(self):
        with (
            patch.object(server, "_settings", return_value={"multiview_order": ["layout"]}),
            patch.object(self.server, "_resolve_layout", return_value=([], "auto", "0")),
            patch.object(self.server, "_worker_config", return_value={"tiles": [{"url": "old"}]}),
        ):
            stopped = self.server._check_live_reload(self.proc, "layout")

        self.assertFalse(stopped)
        self.proc.kill.assert_not_called()

    def test_changed_regex_result_reloads_only_its_worker(self):
        other_proc = Mock()
        self.server._active_procs[other_proc] = {"n": "other", "cfg": {"tiles": [{"url": "other"}]}}
        with (
            patch.object(server, "_settings", return_value={"multiview_order": ["layout", "other"]}),
            patch.object(self.server, "_resolve_layout", return_value=([], "auto", "0")),
            patch.object(self.server, "_worker_config", return_value={"tiles": [{"url": "new-regex-match"}]}),
        ):
            first_check = self.server._check_live_reload(self.proc, "layout")
            stopped = self.server._check_live_reload(self.proc, "layout")

        self.assertFalse(first_check)
        self.assertTrue(stopped)
        self.proc.kill.assert_called_once_with()
        other_proc.kill.assert_not_called()

    def test_cosmetic_tile_metadata_does_not_reload_worker(self):
        self.server._active_procs[self.proc]["cfg"] = {
            "tiles": [{"url": "source", "name": "Old", "logo": "old-logo", "lang": "old"}],
        }
        with (
            patch.object(server, "_settings", return_value={"multiview_order": ["layout"]}),
            patch.object(self.server, "_resolve_layout", return_value=([], "auto", "0")),
            patch.object(self.server, "_worker_config", return_value={
                "tiles": [{"url": "source", "name": "New", "logo": "new-logo", "lang": "new"}],
            }),
        ):
            stopped = self.server._check_live_reload(self.proc, "layout")

        self.assertFalse(stopped)
        self.proc.kill.assert_not_called()

    def test_removed_layout_stops_its_worker(self):
        with patch.object(server, "_settings", return_value={"multiview_order": []}):
            stopped = self.server._check_live_reload(self.proc, "layout")

        self.assertTrue(stopped)
        self.proc.kill.assert_called_once_with()

    def test_invalid_replacement_preserves_running_worker(self):
        with (
            patch.object(server, "_settings", return_value={"multiview_order": ["layout"]}),
            patch.object(self.server, "_resolve_layout", side_effect=LookupError("not enough channels")),
        ):
            stopped = self.server._check_live_reload(self.proc, "layout")

        self.assertFalse(stopped)
        self.proc.kill.assert_not_called()

    def test_old_monitor_cannot_kill_replacement_worker(self):
        replacement = Mock()
        self.server._active_procs.clear()
        self.server._active_procs[replacement] = {"n": "layout", "cfg": {"tiles": [{"url": "new"}]}}

        stopped = self.server._check_live_reload(self.proc, "layout")

        self.assertTrue(stopped)
        replacement.kill.assert_not_called()


class StreamLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.server = server.MultiviewServer("127.0.0.1", 9292)
        self.proc = Mock()
        self.proc.stdout = BytesIO(b"transport-stream")
        self.proc.poll.return_value = None
        self.server._active_procs[self.proc] = {"n": "layout", "cfg": {}}

    def test_worker_eof_is_logged(self):
        with self.assertLogs(server.logger, "INFO") as logs:
            self.assertEqual(list(self.server._pump_stdout(self.proc, "worker layout")), [b"transport-stream"])

        self.assertIn("worker output ended", logs.output[0])

    def test_downstream_disconnect_is_logged(self):
        stream = self.server._pump_stdout(self.proc, "worker layout")
        self.assertEqual(next(stream), b"transport-stream")

        with self.assertLogs(server.logger, "INFO") as logs:
            stream.close()

        self.assertIn("downstream consumer disconnected", logs.output[0])


class PluginReloadActionTests(unittest.TestCase):
    def test_action_reloads_all_active_workers(self):
        running_server = Mock()
        running_server.is_running.return_value = True
        running_server.kill_active_streams.return_value = 2
        server_module = Mock()
        server_module.get_server.return_value = running_server

        with patch("src._server", return_value=server_module):
            result = Plugin.__new__(Plugin).run("reload_active_multiviews", {}, {})

        self.assertEqual(result["status"], "success")
        self.assertIn("2", result["message"])
        running_server.kill_active_streams.assert_called_once_with()

    def test_action_is_safe_when_server_is_not_running(self):
        server_module = Mock()
        server_module.get_server.return_value = None

        with patch("src._server", return_value=server_module):
            result = Plugin.__new__(Plugin).run("reload_active_multiviews", {}, {})

        self.assertEqual(result["status"], "success")
        self.assertIn("0", result["message"])


if __name__ == "__main__":
    unittest.main()
