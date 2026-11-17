"""Ordered, recoverable dispatch of Dispatcharr refresh tasks."""

import tempfile
import unittest
from unittest.mock import Mock, patch

from src import refresh


class RefreshStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = self.directory.name
        self.dispatch = Mock(side_effect=["m3u-task", "epg-task"])

    def start(self, source_id=34):
        with refresh.locked(self.path):
            refresh.start(self.path, 12, source_id, self.dispatch)

    def test_epg_waits_for_successful_m3u(self):
        self.start()
        task = Mock()
        task.ready.return_value = False
        refresh.advance(self.path, self.dispatch, lambda _: task)
        self.dispatch.assert_called_once_with("apps.m3u.tasks.refresh_single_m3u_account", args=[12])
        task.ready.return_value = True
        task.successful.return_value = True
        task.result = "Dispatched jobs complete."
        state = refresh.advance(self.path, self.dispatch, lambda _: task)
        self.dispatch.assert_called_with("apps.epg.tasks.refresh_epg_data", args=[34])
        self.assertEqual(state["phase"], "epg")
        self.assertEqual(state["task_id"], "epg-task")
        state = refresh.advance(self.path, self.dispatch, lambda _: task)
        self.assertEqual(state["phase"], "complete")

    def test_failed_or_skipped_m3u_never_dispatches_epg(self):
        self.start()
        task = Mock()
        task.ready.return_value = True
        task.successful.return_value = True
        task.result = "Task already running"
        state = refresh.advance(self.path, self.dispatch, lambda _: task)
        self.assertEqual(state["phase"], "error")
        self.assertEqual(self.dispatch.call_count, 1)

    def test_ambiguous_dispatch_is_blocked_without_retry(self):
        self.dispatch.side_effect = RuntimeError("connection lost")
        with self.assertRaises(RuntimeError), refresh.locked(self.path):
            refresh.start(self.path, 12, 34, self.dispatch)
        with patch.object(refresh.time, "time", return_value=refresh.read_state(self.path)["started"] + 3601):
            state = refresh.advance(self.path, self.dispatch, Mock())
        self.assertEqual(state["phase"], "blocked")
        self.assertEqual(self.dispatch.call_count, 1)

    def test_m3u_only_completes_without_epg(self):
        self.start(source_id=None)
        task = Mock()
        task.ready.return_value = True
        task.successful.return_value = True
        task.result = "Dispatched jobs complete."
        self.assertEqual(refresh.advance(self.path, self.dispatch, lambda _: task)["phase"], "complete")
        self.assertEqual(self.dispatch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
