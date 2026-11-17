"""Managed listener lifecycle without starting a real network server."""

import sys
import threading
import types
import unittest
from unittest.mock import Mock, patch

from src import server


class ManagedServerTests(unittest.TestCase):
    def setUp(self):
        self.instance = server.MultiviewServer("127.0.0.1", 9292, "/unused")
        self.stop = threading.Event()
        self.listener = Mock()
        self.gevent = types.ModuleType("gevent")
        self.gevent.sleep = Mock(side_effect=lambda _: self.stop.set())
        self.gevent.pywsgi = types.SimpleNamespace(WSGIServer=Mock(return_value=self.listener))
        self.modules = patch.dict(sys.modules, {"gevent": self.gevent})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.addCleanup(server.set_server, None)

    @patch.object(server, "_close_db_connections")
    def test_service_starts_ticks_and_stops_workers(self, close_connections):
        proc = Mock()
        self.instance._active_procs[proc] = {"n": "layout"}
        ready = Mock()
        tick = Mock()

        self.instance.serve(self.stop, on_ready=ready, on_tick=tick)

        self.listener.start.assert_called_once_with()
        ready.assert_called_once_with()
        tick.assert_called_once_with()
        proc.kill.assert_called_once_with()
        self.listener.stop.assert_called_once_with()
        self.assertFalse(self.instance.is_running())
        self.assertIsNone(server.get_server())
        close_connections.assert_called_once_with()

    @patch.object(server, "_close_db_connections")
    def test_stop_before_start_never_binds(self, close_connections):
        self.stop.set()
        self.instance.serve(self.stop)
        self.gevent.pywsgi.WSGIServer.assert_not_called()
        close_connections.assert_called_once_with()

    @patch.object(server, "_close_db_connections")
    def test_bind_failure_retries_then_propagates(self, close_connections):
        self.gevent.sleep.side_effect = None
        self.listener.start.side_effect = OSError("port in use")
        with self.assertRaisesRegex(OSError, "port in use"):
            self.instance.serve(self.stop)
        self.assertEqual(self.listener.start.call_count, 5)
        self.assertEqual(self.gevent.sleep.call_count, 4)
        self.assertFalse(self.instance.is_running())
        close_connections.assert_called_once_with()

    def test_stop_does_not_clear_another_server(self):
        other = object()
        server.set_server(other)
        self.instance.stop()
        self.assertIs(server.get_server(), other)


if __name__ == "__main__":
    unittest.main()
