"""Exercise the actual channel retry loop without multimedia dependencies."""

import importlib.util
from io import BytesIO
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src import server


def load_channel():
    spec = importlib.util.spec_from_file_location("src._test_channel", Path(__file__).resolve().parents[1] / "src/channel.py")
    module = importlib.util.module_from_spec(spec)
    numpy = types.ModuleType("numpy")
    numpy.ndarray = object
    with patch.dict(sys.modules, {"numpy": numpy, "av": types.ModuleType("av")}):
        spec.loader.exec_module(module)
    return module


channel = load_channel()


class ChannelRetryTests(unittest.TestCase):
    def make_channel(self):
        tile = channel.Channel.__new__(channel.Channel)
        tile.name = "test"
        tile.running = True
        tile.vcount = 0
        tile._reconnect_event = threading.Event()
        tile._reconnect_lock = threading.Lock()
        tile._begin_recovery = Mock()
        return tile

    def test_retries_past_twelve_with_capped_delay_and_recovers(self):
        tile = self.make_channel()
        attempts = []
        delays = []

        def open_container():
            attempts.append(1)
            if len(attempts) == 15:
                tile.vcount += 1
            raise OSError("offline")

        def wait(delay):
            delays.append(delay)
            if len(delays) == 16:
                tile.stop()

        tile._open_container = open_container
        with patch.object(tile._reconnect_event, "wait", side_effect=wait), patch.object(channel, "log"):
            tile.run()
        self.assertEqual(len(attempts), 16)
        self.assertEqual(delays[:6], [2, 4, 8, 16, 32, 60])
        self.assertEqual(delays[13], 60)
        self.assertEqual(delays[14:16], [1, 2])

    def test_reconnect_and_stop_interrupt_backoff(self):
        tile = self.make_channel()
        waiting = threading.Event()
        second_attempt = threading.Event()
        calls = []
        original_wait = tile._reconnect_event.wait

        def open_container():
            calls.append(1)
            if len(calls) == 2:
                second_attempt.set()
            raise OSError("offline")

        def wait(delay):
            waiting.set()
            return original_wait(delay)

        tile._open_container = open_container
        with patch.object(tile._reconnect_event, "wait", side_effect=wait), patch.object(channel, "log"):
            thread = threading.Thread(target=tile.run, daemon=True)
            thread.start()
            try:
                self.assertTrue(waiting.wait(1))
                tile.reconnect()
                self.assertTrue(second_attempt.wait(1))
            finally:
                tile.stop()
                thread.join(1)
            self.assertFalse(thread.is_alive())

    def test_reconnect_during_demux_is_not_lost(self):
        tile = self.make_channel()
        stream = Mock()
        cont = Mock()
        cont.streams.video = [stream]
        def packets(*_streams):
            tile.reconnect()
            yield Mock()
        cont.demux.side_effect = packets
        tile.provides_audio = False
        tile.featured = True
        tile._open_container = Mock(return_value=cont)
        with patch.object(tile._reconnect_event, "wait", wraps=tile._reconnect_event.wait) as wait, patch.object(channel, "log"):
            def stop_on_second_open():
                tile.stop()
                raise OSError("stopped")
            tile._open_container.side_effect = lambda: cont if not cont.close.called else stop_on_second_open()
            tile.run()
        self.assertEqual(tile._open_container.call_count, 2)
        self.assertTrue(wait.called)


class ServerReconnectTests(unittest.TestCase):
    def test_sends_to_all_matching_workers_despite_broken_pipe(self):
        instance = server.MultiviewServer("127.0.0.1", 9292, "/unused")
        broken, good, other = Mock(), Mock(), Mock()
        broken.stdin.write.side_effect = BrokenPipeError()
        instance._active_procs = {broken: {"n": "a"}, good: {"n": "a"}, other: {"n": "b"}}
        self.assertTrue(instance.reconnect_channel("a", 2))
        good.stdin.write.assert_called_once_with(b'{"cmd": "reconnect_channel", "idx": 2}\n')
        good.stdin.flush.assert_called_once()
        other.stdin.write.assert_not_called()
        self.assertFalse(instance.reconnect_channel("missing", 0))


class DecodeLoggingTests(unittest.TestCase):
    def make_channel(self, accelerator):
        tile = channel.Channel.__new__(channel.Channel)
        tile.name = "test"
        tile.url = "source"
        tile.hwaccel = accelerator
        tile.hwaccel_device = None
        tile._reported_decode_mode = None
        return tile

    def test_hardware_open_is_reported_once_across_reconnects(self):
        tile = self.make_channel("qsv")
        hw = types.ModuleType("av.codec.hwaccel")
        hw.HWAccel = Mock(return_value=object())
        hw.hwdevices_available = Mock(return_value=["qsv"])
        with patch.dict(sys.modules, {"av.codec": types.ModuleType("av.codec"), "av.codec.hwaccel": hw}), \
             patch.object(channel.av, "open", create=True, return_value=object()), patch.object(channel, "log") as log:
            tile._open_container()
            tile._open_container()
        log.assert_called_once_with("channel test: opened container with hardware decode=qsv", "INFO")

    def test_fallback_warning_and_software_mode_do_not_depend_on_exception_text(self):
        tile = self.make_channel("qsv")
        hw = types.ModuleType("av.codec.hwaccel")
        hw.HWAccel = Mock(side_effect=ValueError("no compatible stream"))
        hw.hwdevices_available = Mock(return_value=["qsv"])
        with patch.dict(sys.modules, {"av.codec": types.ModuleType("av.codec"), "av.codec.hwaccel": hw}), \
             patch.object(channel.av, "open", create=True, return_value=object()), patch.object(channel, "log") as log:
            tile._open_container()
            tile._open_container()
        self.assertEqual(log.call_count, 2)
        self.assertEqual(log.call_args_list[0].args[1], "WARNING")
        self.assertIn("no compatible stream", log.call_args_list[0].args[0])
        self.assertEqual(log.call_args_list[1].args[1], "INFO")
        self.assertIsNone(tile.hwaccel)
        hw.HWAccel.assert_called_once()

    def test_software_only_is_reported_once(self):
        tile = self.make_channel(None)
        with patch.object(channel.av, "open", create=True, return_value=object()), patch.object(channel, "log") as log:
            tile._open_container()
            tile._open_container()
        log.assert_called_once_with("channel test: opened container with software decode", "INFO")

    def test_stderr_routes_explicit_levels_and_preserves_tracebacks(self):
        instance = server.MultiviewServer("127.0.0.1", 9292, "/unused")
        proc = Mock(stderr=BytesIO(
            b"[mvworker] INFO: channel failed channel: hardware open\n"
            b"[mvworker] WARNING: fallback without marker\n"
            b"[mvworker] normal output\n"
            b"Traceback (most recent call last):\n"
            b"  stack line\n"
        ))
        with patch.object(server.logger, "info") as info, patch.object(server.logger, "warning") as warning, \
             patch.object(server.logger, "debug") as debug:
            instance._drain_stderr(proc, "worker")
        self.assertEqual(info.call_count, 1)
        self.assertEqual(warning.call_count, 3)
        self.assertEqual(debug.call_count, 1)


if __name__ == "__main__":
    unittest.main()
