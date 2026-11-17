"""Ordered, recoverable dispatch of Dispatcharr refresh tasks."""

import tempfile
import sys
import types
from datetime import datetime, timezone
import unittest
from unittest.mock import Mock, patch

from src import Plugin, refresh


class RefreshStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = self.directory.name
        self.dispatch = Mock(side_effect=["m3u-task", "epg-task"])
        self.dispatch_plugin = Mock(return_value="retry-task")
        validate_m3u = patch.object(refresh, "_validate_m3u", side_effect=lambda state, result: None if result == "Dispatched jobs complete." else "M3U failed")
        validate_epg = patch.object(refresh, "_validate_epg", return_value=None)
        validate_m3u.start()
        validate_epg.start()
        self.addCleanup(validate_m3u.stop)
        self.addCleanup(validate_epg.stop)

    def start(self, source_id=34):
        with refresh.locked(self.path):
            refresh.start(self.path, 12, source_id, self.dispatch)

    def test_epg_waits_for_successful_m3u(self):
        self.start()
        task = Mock()
        task.ready.return_value = False
        refresh.advance(self.path, self.dispatch, lambda _: task, self.dispatch_plugin)
        self.dispatch.assert_called_once_with("apps.m3u.tasks.refresh_single_m3u_account", args=[12])
        task.ready.return_value = True
        task.successful.return_value = True
        task.result = "Dispatched jobs complete."
        state = refresh.advance(self.path, self.dispatch, lambda _: task, self.dispatch_plugin)
        self.dispatch.assert_called_with("apps.epg.tasks.refresh_epg_data", args=[34])
        self.assertEqual(state["phase"], "epg")
        self.assertEqual(state["task_id"], "epg-task")
        state = refresh.advance(self.path, self.dispatch, lambda _: task, self.dispatch_plugin)
        self.assertEqual(state["phase"], "complete")

    def test_failed_or_skipped_m3u_never_dispatches_epg(self):
        self.start()
        task = Mock()
        task.ready.return_value = True
        task.successful.return_value = True
        task.result = "Task already running"
        state = refresh.advance(self.path, self.dispatch, lambda _: task, self.dispatch_plugin)
        self.assertEqual(state["phase"], "error")
        self.assertEqual(self.dispatch.call_count, 1)

    def test_epg_failure_does_not_mark_refresh_complete(self):
        self.start()
        task = Mock()
        task.ready.return_value = True
        task.successful.return_value = True
        task.result = "Dispatched jobs complete."
        refresh.advance(self.path, self.dispatch, lambda _: task, self.dispatch_plugin)
        refresh._validate_epg.return_value = "EPG still parsing"
        state = refresh.advance(self.path, self.dispatch, lambda _: task, self.dispatch_plugin)
        self.assertEqual(state["phase"], "blocked")
        self.assertEqual(state["error"], "EPG still parsing")
        refresh._validate_epg.return_value = None
        state = refresh.advance(self.path, self.dispatch, lambda _: task, self.dispatch_plugin)
        self.assertEqual(state["phase"], "complete")
        self.assertNotIn("error", state)

    def test_ambiguous_dispatch_is_blocked_without_retry(self):
        self.dispatch.side_effect = RuntimeError("connection lost")
        with self.assertRaises(RuntimeError), refresh.locked(self.path):
            refresh.start(self.path, 12, 34, self.dispatch)
        with patch.object(refresh.time, "time", return_value=refresh.read_state(self.path)["started"] + 3601):
            state = refresh.advance(self.path, self.dispatch, Mock(), self.dispatch_plugin)
        self.assertEqual(state["phase"], "blocked")
        self.assertEqual(self.dispatch.call_count, 1)

    def test_m3u_only_completes_without_epg(self):
        self.start(source_id=None)
        task = Mock()
        task.ready.return_value = True
        task.successful.return_value = True
        task.result = "Dispatched jobs complete."
        self.assertEqual(refresh.advance(self.path, self.dispatch, lambda _: task, self.dispatch_plugin)["phase"], "complete")
        self.assertEqual(self.dispatch.call_count, 1)

    def test_overlapping_request_is_coalesced_and_dispatched_after_completion(self):
        self.start(source_id=None)
        plugin = Plugin.__new__(Plugin)
        context = {"data_dir": self.path}
        result = plugin._generate_m3u(context, {})
        self.assertEqual(result["status"], "success")
        self.assertTrue(refresh.read_state(self.path)["pending"])
        task = Mock()
        task.ready.return_value = True
        task.successful.return_value = True
        task.result = "Dispatched jobs complete."
        state = refresh.advance(self.path, self.dispatch, lambda _: task, self.dispatch_plugin)
        self.assertEqual(state["phase"], "retry_pending")
        state = refresh.advance(self.path, self.dispatch, lambda _: task, self.dispatch_plugin)
        self.assertEqual(state["phase"], "retry_queued")
        self.dispatch_plugin.assert_called_once_with("generate_m3u", {"refresh_retry_token": state["retry_token"]})
        with patch.object(plugin, "_generate_m3u_locked", return_value={"status": "success"}) as generate:
            plugin._generate_m3u(context, {"refresh_retry_token": state["retry_token"]})
        generate.assert_called_once_with(context, refresh, pending=False)

    def test_failed_retry_dispatch_is_not_automatically_duplicated(self):
        refresh.write_state(self.path, {"phase": "retry_pending", "pending": True})
        self.dispatch_plugin.side_effect = RuntimeError("outcome unknown")
        with self.assertRaises(RuntimeError):
            refresh.advance(self.path, self.dispatch, Mock(), self.dispatch_plugin)
        state = refresh.read_state(self.path)
        self.assertEqual(state["phase"], "dispatching_retry")
        with patch.object(refresh.time, "time", return_value=state["started"] + 3601):
            state = refresh.advance(self.path, self.dispatch, Mock(), self.dispatch_plugin)
        self.assertEqual(state["phase"], "blocked")
        self.dispatch_plugin.assert_called_once()

    def test_request_during_queued_retry_schedules_another_pass(self):
        refresh.write_state(self.path, {
            "phase": "retry_queued", "task_id": "retry-task", "retry_token": "expected", "pending": False,
        })
        plugin = Plugin.__new__(Plugin)
        context = {"data_dir": self.path}
        self.assertEqual(plugin._generate_m3u(context, {})["status"], "success")
        with patch.object(plugin, "_generate_m3u_locked", return_value={"status": "success"}) as generate:
            plugin._generate_m3u(context, {"refresh_retry_token": "expected"})
        generate.assert_called_once_with(context, refresh, pending=True)

    def test_timed_out_queued_retry_unblocks_after_task_ends(self):
        refresh.write_state(self.path, {
            "phase": "retry_queued", "started": 0, "task_id": "retry-task", "retry_token": "expected",
        })
        task = Mock()
        task.ready.return_value = False
        with patch.object(refresh.time, "time", return_value=3601):
            state = refresh.advance(self.path, self.dispatch, lambda _: task, self.dispatch_plugin)
        self.assertEqual(state["phase"], "blocked")
        task.ready.return_value = True
        state = refresh.advance(self.path, self.dispatch, lambda _: task, self.dispatch_plugin)
        self.assertEqual(state["phase"], "error")
        self.assertNotIn("blocked_phase", state)

    def test_blocked_refresh_does_not_claim_to_queue_a_new_run(self):
        refresh.write_state(self.path, {"phase": "blocked", "error": "outcome unknown"})
        plugin = Plugin.__new__(Plugin)
        result = plugin._generate_m3u({"data_dir": self.path}, {})
        self.assertEqual(result["status"], "error")
        self.assertEqual(refresh.read_state(self.path)["phase"], "blocked")

    def test_background_poll_resumes_when_known_m3u_task_finishes(self):
        self.start()
        task = Mock()
        task.ready.return_value = False
        started = refresh.read_state(self.path)["started"]
        with patch.object(refresh.time, "time", return_value=started + 3601):
            refresh.advance(self.path, self.dispatch, lambda _: task, self.dispatch_plugin)
        refresh.advance(self.path, self.dispatch, lambda _: task, self.dispatch_plugin)
        self.assertEqual(refresh.read_state(self.path)["phase"], "blocked")
        task.ready.return_value = True
        task.successful.return_value = True
        task.result = "Dispatched jobs complete."
        self.assertEqual(refresh.advance(self.path, self.dispatch, lambda _: task, self.dispatch_plugin)["phase"], "epg")

    def test_ambiguous_submission_cannot_be_cleared_as_a_known_task(self):
        self.dispatch.side_effect = RuntimeError("connection lost")
        with self.assertRaises(RuntimeError), refresh.locked(self.path):
            refresh.start(self.path, 12, 34, self.dispatch)
        started = refresh.read_state(self.path)["started"]
        with patch.object(refresh.time, "time", return_value=started + 3601):
            refresh.advance(self.path, self.dispatch, Mock(), self.dispatch_plugin)
        result_for = Mock()
        refresh.advance(self.path, self.dispatch, result_for, self.dispatch_plugin)
        result_for.assert_not_called()
        self.assertEqual(refresh.read_state(self.path)["phase"], "blocked")


class HostResultValidationTests(unittest.TestCase):
    def test_m3u_requires_success_newer_than_dispatch(self):
        account = types.SimpleNamespace(status="success", updated_at=datetime.fromtimestamp(99, timezone.utc))
        model = types.SimpleNamespace(Status=types.SimpleNamespace(SUCCESS="success"), objects=Mock())
        model.objects.get.return_value = account
        modules = {name: types.ModuleType(name) for name in ("apps", "apps.m3u", "apps.m3u.models")}
        modules["apps.m3u.models"].M3UAccount = model
        state = {"account_id": 1, "started": 100}
        with patch.dict(sys.modules, modules):
            self.assertIn("predates", refresh._validate_m3u(state, "Dispatched jobs complete."))
            account.updated_at = datetime.fromtimestamp(101, timezone.utc)
            self.assertIsNone(refresh._validate_m3u(state, "Dispatched jobs complete."))
            account.status = "error"
            self.assertIn("did not report", refresh._validate_m3u(state, "Dispatched jobs complete."))
            self.assertIn("did not complete", refresh._validate_m3u(state, "Task already running"))

    def test_epg_requires_success_newer_than_dispatch(self):
        source = types.SimpleNamespace(status="success", updated_at=datetime.fromtimestamp(99, timezone.utc))
        model = types.SimpleNamespace(STATUS_SUCCESS="success", objects=Mock())
        model.objects.get.return_value = source
        modules = {name: types.ModuleType(name) for name in ("apps", "apps.epg", "apps.epg.models")}
        modules["apps.epg.models"].EPGSource = model
        state = {"source_id": 1, "started": 100}
        with patch.dict(sys.modules, modules):
            self.assertIn("predates", refresh._validate_epg(state))
            source.updated_at = datetime.fromtimestamp(101, timezone.utc)
            self.assertIsNone(refresh._validate_epg(state))
            source.status = "parsing"
            self.assertIn("did not report", refresh._validate_epg(state))

if __name__ == "__main__":
    unittest.main()
