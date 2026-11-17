"""Tests for the existing-listener check during plugin startup."""

import unittest
from unittest.mock import MagicMock, patch

import src


class AutostartTests(unittest.TestCase):
    def setUp(self):
        self.plugin = src.Plugin.__new__(src.Plugin)

    @patch.object(src, "_server")
    @patch.object(src.socket, "socket")
    def test_existing_listener_uses_guardable_socket_and_closes_it(self, socket_factory, server):
        server.return_value.get_server.return_value = None
        sock = socket_factory.return_value
        self.plugin._start_server = MagicMock()

        self.plugin._autostart()

        socket_factory.assert_called_once_with(src.socket.AF_INET, src.socket.SOCK_STREAM)
        sock.settimeout.assert_called_once_with(0.5)
        sock.connect.assert_called_once_with(("127.0.0.1", src.DEFAULT_SERVER_PORT))
        sock.close.assert_called_once_with()
        self.plugin._start_server.assert_not_called()

    @patch.object(src, "_server")
    @patch.object(src.socket, "socket")
    def test_failed_probe_closes_socket_and_attempts_start(self, socket_factory, server):
        server.return_value.get_server.return_value = None
        sock = socket_factory.return_value
        sock.connect.side_effect = OSError("connection refused")
        self.plugin._start_server = MagicMock(return_value={"status": "error", "message": "unavailable"})

        self.plugin._autostart()

        sock.close.assert_called_once_with()
        self.plugin._start_server.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
