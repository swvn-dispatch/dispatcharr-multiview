"""Bounded, non-interrupting diagnostic collection."""

import io
import json
import unittest
from unittest.mock import Mock, patch

from src import Plugin, server
from src.diagnostics import DiagnosticSampler
from src.frame_policy import PresentationStats


class FakeChannel:
    def __init__(self):
        self.vcount = self.scaled_vcount = self.reduced_vcount = 0
        self.stats = None

    def start_presentation_stats(self):
        self.stats = PresentationStats()

    def take_presentation_stats(self):
        stats = self.stats.take()
        self.stats = None
        return stats, 2


class DiagnosticSamplingTests(unittest.TestCase):
    def test_worker_collects_once_and_emits_sanitized_result(self):
        channel = FakeChannel()
        timer = Mock()
        emitted = []
        clock = Mock(side_effect=[100, 220])
        sampler = DiagnosticSampler([channel], emitted.append, clock, timer)
        self.assertTrue(sampler.start("a" * 32))
        self.assertFalse(sampler.start("b" * 32))
        channel.vcount = channel.scaled_vcount = 3600
        channel.stats.record(due=2, lag=0.02)
        callback = timer.call_args.args[1]
        callback(*timer.call_args.kwargs["args"])
        result = emitted[0]
        self.assertEqual(result["duration_seconds"], 120)
        self.assertEqual(result["tiles"][0]["decoded_fps"], 30)
        self.assertEqual(result["tiles"][0]["skipped_frames"], 1)
        self.assertIsNone(channel.stats)
        self.assertNotIn("url", json.dumps(result))

    def test_service_collects_worker_result_without_killing_worker(self):
        instance = server.MultiviewServer("127.0.0.1", 9292, "/unused")
        proc = Mock()
        proc.stdin = io.BytesIO()
        instance._active_procs[proc] = {"n": "layout1"}
        status, result = instance.start_diagnostic_sample()
        self.assertEqual(status, "200 OK")
        sample_id = result["sample_id"]
        self.assertEqual(json.loads(proc.stdin.getvalue())["sample_id"], sample_id)
        self.assertEqual(instance.start_diagnostic_sample()[0], "409 Conflict")
        response = {"sample_id": sample_id, "duration_seconds": 120, "tiles": []}
        proc.stderr = [b"[mvworker] DIAGNOSTIC " + json.dumps(response).encode() + b"\n"]
        instance._drain_stderr(proc, "worker")
        _, result = instance.diagnostic_sample_status(sample_id)
        self.assertEqual(result["pending"], 0)
        self.assertEqual(result["workers"][0]["result"]["tiles"], [])
        proc.kill.assert_not_called()

    def test_stream_stopping_is_reported(self):
        instance = server.MultiviewServer("127.0.0.1", 9292, "/unused")
        proc = Mock()
        proc.stdin = io.BytesIO()
        instance._active_procs[proc] = {"n": "layout1"}
        _, sample = instance.start_diagnostic_sample()
        instance._active_procs.clear()
        _, result = instance.diagnostic_sample_status(sample["sample_id"])
        self.assertEqual(result["pending"], 0)
        self.assertIn("ended", result["workers"][0]["error"])

    def test_no_active_streams(self):
        instance = server.MultiviewServer("127.0.0.1", 9292, "/unused")
        self.assertEqual(instance.start_diagnostic_sample()[0], "409 Conflict")

    def test_diagnostic_endpoint_rejects_remote_and_cross_origin(self):
        instance = server.MultiviewServer("127.0.0.1", 9292, "/unused")
        start = Mock()
        environ = {"PATH_INFO": "/internal/diagnostics", "REQUEST_METHOD": "POST",
                   "REMOTE_ADDR": "192.0.2.1", "CONTENT_TYPE": "application/json"}
        instance._wsgi_app(environ, start)
        self.assertEqual(start.call_args.args[0], "403 Forbidden")
        environ.update(REMOTE_ADDR="127.0.0.1", HTTP_ORIGIN="https://example.test")
        instance._wsgi_app(environ, start)
        self.assertEqual(start.call_args.args[0], "403 Forbidden")

    def test_action_returns_task_history_output(self):
        sample_id = "a" * 32
        conn = Mock()
        conn.getresponse.side_effect = [
            Mock(status=200, read=Mock(return_value=json.dumps({"sample_id": sample_id, "worker_count": 1}).encode())),
            Mock(status=200, read=Mock(return_value=b'{"pending":0,"workers":[{"layout_id":"x","result":{"tiles":[]}}]}')),
        ]
        with patch("src.http.client.HTTPConnection", return_value=conn):
            result = Plugin().run("sample_diagnostics", {}, {})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["requested_duration_seconds"], 120)
        self.assertEqual(len(result["multiviews"]), 1)

    def test_action_reports_no_active_streams(self):
        conn = Mock()
        conn.getresponse.return_value = Mock(status=409, read=Mock(return_value=b'{"error":"no active multiviews"}'))
        with patch("src.http.client.HTTPConnection", return_value=conn):
            result = Plugin().run("sample_diagnostics", {}, {})
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["message"], "no active multiviews")
