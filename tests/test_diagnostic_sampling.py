"""Bounded, non-interrupting diagnostic collection."""

import io
import json
import unittest
from unittest.mock import Mock, patch

from src import Plugin, server
from src.diagnostics import DiagnosticSampler, OutputMeter
from src.frame_policy import PresentationStats


class FakeChannel:
    def __init__(self):
        self.name = "Example Channel"
        self.vcount = self.scaled_vcount = self.reduced_vcount = 0
        self.stats = None

    def start_presentation_stats(self):
        self.stats = PresentationStats()

    def diagnostic_source_info(self):
        return {"source_fps_estimate": 30.0, "source_width": 1920, "source_height": 1080}

    def take_presentation_stats(self, continue_sampling=False):
        stats = self.stats.take()
        if not continue_sampling:
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
        self.assertEqual(result["tiles"][0]["channel_name"], "Example Channel")
        self.assertIsNone(channel.stats)
        self.assertNotIn("url", json.dumps(result))

    def test_four_intervals_and_aggregate(self):
        channel = FakeChannel()
        timer = Mock()
        emitted = []
        sampler = DiagnosticSampler([channel], emitted.append,
                                    Mock(side_effect=[0, 30, 60, 90, 120]), timer)
        self.assertTrue(sampler.start("a" * 32))
        for i in range(4):
            channel.vcount += 900
            channel.scaled_vcount += 900
            channel.stats.record(due=1, lag=(i + 1) / 1000)
            callback = timer.call_args.args[1]
            callback(*timer.call_args.kwargs["args"])
        result = emitted[0]
        self.assertEqual([item["elapsed_seconds"] for item in result["intervals"]], [30, 60, 90, 120])
        self.assertEqual([item["tiles"][0]["fresh_frames"] for item in result["intervals"]], [1, 1, 1, 1])
        self.assertEqual(result["tiles"][0]["fresh_frames"], 4)
        self.assertEqual(result["tiles"][0]["decoded_fps"], 30)
        self.assertEqual(result["tiles"][0]["pts_age_mean_ms"], 2.5)
        self.assertEqual(result["tiles"][0]["pts_age_max_ms"], 4.0)

    def test_one_second_intervals_include_timing_and_output_metrics(self):
        channel = FakeChannel()
        timer = Mock()
        emitted = []
        meter = OutputMeter()
        sampler = DiagnosticSampler([channel], emitted.append,
                                    Mock(side_effect=[0, 1, 2]), timer, meter)
        self.assertTrue(sampler.start("a" * 32, duration=2))
        self.assertEqual(timer.call_args.args[0], 1)
        for i in range(2):
            channel.vcount += 30
            channel.scaled_vcount += 30
            channel.stats.record(due=1, lag=0.01, queue_depth=i)
            channel.stats.enqueue_lead(-0.005 if i == 0 else 0.04)
            if i == 0:
                channel.stats.event("recovery", 0.5)
            meter.record(0.002)
            callback = timer.call_args.args[1]
            callback(*timer.call_args.kwargs["args"])
        result = emitted[0]
        self.assertEqual(len(result["intervals"]), 2)
        tile = result["tiles"][0]
        self.assertEqual(tile["queue_depth_min"], 0)
        self.assertEqual(tile["queue_depth_max"], 1)
        self.assertEqual(tile["queue_empty_percent"], 50)
        self.assertEqual(tile["events"], [{"type": "recovery", "elapsed_seconds": 0.5}])
        self.assertEqual(tile["source_fps_estimate"], 30.0)
        self.assertEqual(result["output"]["encoder_pipe_write_ms"], 4.0)
        self.assertEqual(result["output"]["fps"], 1.0)

    def test_two_minute_timeline_fits_action_response(self):
        channels = [FakeChannel() for _ in range(4)]
        timer = Mock()
        emitted = []
        sampler = DiagnosticSampler(channels, emitted.append,
                                    Mock(side_effect=range(121)), timer)
        self.assertTrue(sampler.start("a" * 32))
        for _ in range(120):
            for channel in channels:
                channel.vcount += 30
                channel.scaled_vcount += 30
                channel.stats.record(due=1, lag=0.01, queue_depth=2)
            callback = timer.call_args.args[1]
            callback(*timer.call_args.kwargs["args"])
        self.assertEqual(len(emitted[0]["intervals"]), 120)
        self.assertLess(len(json.dumps(emitted[0]).encode()), 4 * 1024 * 1024)

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

    def test_diagnostic_endpoint_serializes_start_and_status(self):
        instance = server.MultiviewServer("127.0.0.1", 9292, "/unused")
        proc = Mock()
        proc.stdin = io.BytesIO()
        instance._active_procs[proc] = {"n": "layout1"}
        start = Mock()
        environ = {"PATH_INFO": "/internal/diagnostics", "REQUEST_METHOD": "POST",
                   "REMOTE_ADDR": "127.0.0.1", "CONTENT_TYPE": "application/json"}
        body = b"".join(instance._wsgi_app(environ, start))
        self.assertEqual(start.call_args.args[0], "200 OK")
        sample_id = json.loads(body)["sample_id"]
        environ.update(PATH_INFO=f"/internal/diagnostics/{sample_id}", REQUEST_METHOD="GET")
        body = b"".join(instance._wsgi_app(environ, start))
        self.assertEqual(start.call_args.args[0], "200 OK")
        self.assertEqual(json.loads(body)["pending"], 1)

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

    def test_action_reports_countdown_and_finishing(self):
        sample_id = "a" * 32
        conn = Mock()
        responses = [
            {"sample_id": sample_id, "worker_count": 1},
            {"pending": 1, "workers": []},
            {"pending": 1, "workers": []},
            {"pending": 1, "workers": []},
            {"pending": 0, "workers": [{"layout_id": "x", "result": {}}]},
        ]
        conn.getresponse.side_effect = [Mock(status=200, read=Mock(return_value=json.dumps(item).encode()))
                                        for item in responses]
        progress = Mock()
        with patch("src.http.client.HTTPConnection", return_value=conn), \
             patch("src.time.monotonic", side_effect=[0, 0, 65, 121, 122]), \
             patch("src.time.sleep"):
            result = Plugin().run("sample_diagnostics", {}, {"report_progress": progress})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(progress.call_args_list[0].args, (0, "Sampling active multiviews: 2:00 remaining"))
        self.assertEqual(progress.call_args_list[1].args, (54, "Sampling active multiviews: 0:55 remaining"))
        self.assertEqual(progress.call_args_list[2].args, (99, "Finishing diagnostic sample"))
