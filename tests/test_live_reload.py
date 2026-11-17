"""Regression coverage for automatic active-multiview reloads."""

import unittest
import json
import http.client
import sys
import types
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

    def test_tiles_hold_last_good_frame_for_fifteen_seconds(self):
        fields = config.build_plugin_fields({})

        self.assertNotIn("tile_hold_seconds", [field["id"] for field in fields])
        self.assertEqual(server.TILE_HOLD_SECONDS, 15)

    def test_worker_config_uses_live_edge_child_urls(self):
        cfg = server.MultiviewServer("127.0.0.1", 9292, "/unused")._worker_config(
            [{"id": 1, "name": "One", "logo": None}, {"id": 2, "name": "Two", "logo": None}],
            "auto", "0", {},
        )

        self.assertEqual(
            [tile["url"] for tile in cfg["tiles"]],
            [
                "http://127.0.0.1:9292/internal/realsrc/1",
                "http://127.0.0.1:9292/internal/realsrc/2",
            ],
        )
        self.assertEqual([tile["tile_hold_secs"] for tile in cfg["tiles"]], [15, 15])


class ActiveWorkerReloadTests(unittest.TestCase):
    def setUp(self):
        self.server = server.MultiviewServer("127.0.0.1", 9292, "/unused")
        self.proc = Mock()
        self.server._active_procs[self.proc] = {"n": "layout", "cfg": {"tiles": [{"url": "old"}]}}

    def test_manual_interval_has_no_seconds_value(self):
        self.assertIsNone(server._live_reload_interval_secs({"live_reload_interval": "manual"}))
        self.assertEqual(server._live_reload_interval_secs({"live_reload_interval": "15"}), 15)

    def test_enabling_auto_reload_is_detected_for_active_worker(self):
        fake_gevent = types.ModuleType("gevent")
        fake_gevent.sleep = Mock()
        with (
            patch.dict(sys.modules, {"gevent": fake_gevent}),
            patch.object(server, "_settings", side_effect=[
                {"live_reload_interval": "manual"},
                {"live_reload_interval": "15"},
                {"live_reload_interval": "15"},
            ]),
            patch.object(server, "_close_db_connections"),
            patch.object(self.server, "_check_live_reload", return_value=True) as check,
        ):
            self.server._monitor_live_reload(self.proc, "layout")
        self.assertEqual(fake_gevent.sleep.call_args_list, [unittest.mock.call(15), unittest.mock.call(15)])
        check.assert_called_once_with(self.proc, "layout")

    def test_switching_to_manual_during_sleep_skips_reload(self):
        fake_gevent = types.ModuleType("gevent")

        def sleep(_interval):
            if fake_gevent.sleep.call_count == 2:
                self.server._active_procs.pop(self.proc)

        fake_gevent.sleep = Mock(side_effect=sleep)
        with (
            patch.dict(sys.modules, {"gevent": fake_gevent}),
            patch.object(server, "_settings", side_effect=[
                {"live_reload_interval": "15"},
                {"live_reload_interval": "manual"},
                {"live_reload_interval": "manual"},
            ]),
            patch.object(server, "_close_db_connections"),
            patch.object(self.server, "_check_live_reload") as check,
        ):
            self.server._monitor_live_reload(self.proc, "layout")
        check.assert_not_called()

    def test_database_failure_does_not_kill_active_worker(self):
        fake_gevent = types.ModuleType("gevent")
        fake_gevent.sleep = Mock(side_effect=lambda _: self.server._active_procs.pop(self.proc))
        with (
            patch.dict(sys.modules, {"gevent": fake_gevent}),
            patch.object(server, "_settings", side_effect=RuntimeError("database unavailable")),
            patch.object(server, "_close_db_connections") as close,
            patch.object(self.server, "_check_live_reload") as check,
            self.assertLogs(server.logger, level="WARNING"),
        ):
            self.server._monitor_live_reload(self.proc, "layout")
        close.assert_called_once_with()
        check.assert_not_called()
        self.proc.kill.assert_not_called()

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
        self.server = server.MultiviewServer("127.0.0.1", 9292, "/unused")
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
    def test_action_uses_loopback_for_zero_or_multiple_workers(self):
        for count in (0, 2):
            with self.subTest(count=count), patch("src.http.client.HTTPConnection") as factory:
                conn = factory.return_value
                conn.getresponse.return_value.status = 200
                conn.getresponse.return_value.read.return_value = json.dumps({"count": count}).encode()
                result = Plugin.__new__(Plugin).run("reload_active_multiviews", {}, {})
                self.assertEqual(result["status"], "success")
                self.assertIn(str(count), result["message"])
                factory.assert_called_once_with("127.0.0.1", 9292, timeout=5)
                conn.request.assert_called_once_with("POST", "/internal/reload", body=b"{}", headers={"Content-Type": "application/json"})
                conn.close.assert_called_once()

    def test_action_reports_transport_and_invalid_response_errors(self):
        for status, body, error in ((200, b'{}', None), (200, b'{"count": true}', None),
                                    (200, b'not json', None), (500, b'{}', None),
                                    (200, b'{}', TimeoutError("timeout")),
                                    (200, b'{}', http.client.HTTPException("bad response"))):
            with self.subTest(status=status, body=body, error=error), patch("src.http.client.HTTPConnection") as factory:
                conn = factory.return_value
                conn.getresponse.return_value.status = status
                conn.getresponse.return_value.read.return_value = body
                conn.request.side_effect = error
                result = Plugin.__new__(Plugin).run("reload_active_multiviews", {}, {})
                self.assertEqual(result["status"], "error")
                conn.close.assert_called_once()


class InternalReloadRouteTests(unittest.TestCase):
    def setUp(self):
        self.server = server.MultiviewServer("127.0.0.1", 9292, "/unused")
        self.proc = Mock()
        self.server._active_procs[self.proc] = {"n": "layout", "cfg": {}}

    def request(self, **overrides):
        environ = {"PATH_INFO": "/internal/reload", "REMOTE_ADDR": "127.0.0.1",
                   "REQUEST_METHOD": "POST", "CONTENT_TYPE": "application/json"}
        environ.update(overrides)
        start = Mock()
        with patch.object(server, "_settings", return_value={"dash_enabled": "disabled"}):
            body = b"".join(self.server.wsgi_app(environ, start))
        return start.call_args.args[0], body

    def test_route_reloads_with_dashboard_disabled(self):
        status, body = self.request()
        self.assertEqual(status, "200 OK")
        self.assertEqual(json.loads(body), {"count": 1})
        self.proc.kill.assert_called_once_with()

    def test_route_rejects_remote_methods_and_browser_requests(self):
        for overrides, expected in (({"REMOTE_ADDR": "192.0.2.1"}, "403"),
                                    ({"REQUEST_METHOD": "GET"}, "405"),
                                    ({"HTTP_ORIGIN": "https://example.com"}, "403"),
                                    ({"CONTENT_TYPE": "text/plain"}, "403")):
            with self.subTest(overrides=overrides):
                status, _ = self.request(**overrides)
                self.assertTrue(status.startswith(expected))
                self.proc.kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
