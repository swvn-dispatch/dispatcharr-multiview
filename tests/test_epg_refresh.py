"""Regression tests for multiview's generated EPG refresh contract."""

import tempfile
import unittest
from unittest.mock import Mock, patch

from src import Plugin, _m3u_content
from src import epg
from src import refresh


class EPGDataCleanupTests(unittest.TestCase):
    def test_active_tvg_ids_use_stable_layout_ids(self):
        self.assertEqual(
            epg._active_tvg_ids(["cafe1234", "beef5678"]),
            {"mv-cafe1234", "mv-beef5678"},
        )

    def test_stale_source_rows_are_deleted(self):
        rows = Mock()
        stale_rows = Mock()
        stale_rows.delete.return_value = (3, {"epg.EPGData": 3})
        rows.exclude.return_value = stale_rows
        source = Mock(epgs=rows)

        deleted = epg._remove_stale_epg_data(source, {"mv-current"})

        rows.exclude.assert_called_once_with(tvg_id__in={"mv-current"})
        stale_rows.delete.assert_called_once_with()
        self.assertEqual(deleted, 3)


class M3UOrderTests(unittest.TestCase):
    def test_layout_order_sets_playlist_order_and_channel_numbers(self):
        content = _m3u_content(
            {
                "multiview_second_name": "Second",
                "multiview_first_name": "First",
            },
            ["second", "first"],
        )

        self.assertLess(content.index('tvg-id="mv-second" tvg-chno="1"'), content.index('tvg-id="mv-first" tvg-chno="2"'))


class RefreshOrderTests(unittest.TestCase):
    def test_m3u_refresh_precedes_epg_refresh(self):
        with tempfile.TemporaryDirectory() as data_dir:
            dispatch = Mock(side_effect=["m3u-task", "epg-task"])
            refresh.start(data_dir, 12, 34, ["abcdef01"], dispatch)
            task = Mock(result="Dispatched jobs complete.")
            task.ready.return_value = True
            task.successful.return_value = True
            with patch.object(refresh, "remove_stale"), patch.object(refresh, "layout_order_changed", return_value=False):
                state = refresh.advance(data_dir, dispatch, Mock(), lambda _: task, lambda *_: None)
            self.assertEqual(state["phase"], "epg")
            self.assertEqual(dispatch.call_args_list[0].args[0], "apps.m3u.tasks.refresh_single_m3u_account")
            self.assertEqual(dispatch.call_args_list[1].args[0], "apps.epg.tasks.refresh_epg_data")

    def test_failed_or_unverified_m3u_never_queues_epg(self):
        with tempfile.TemporaryDirectory() as data_dir:
            dispatch = Mock(return_value="m3u-task")
            refresh.start(data_dir, 12, 34, [], dispatch)
            task = Mock(result="Task already running")
            task.ready.return_value = True
            task.successful.return_value = True
            state = refresh.advance(data_dir, dispatch, Mock(), lambda _: task, lambda *_: "M3U not verified")
            self.assertEqual(state["phase"], "error")
            dispatch.assert_called_once()

    def test_pending_refresh_waits_for_existing_work(self):
        with tempfile.TemporaryDirectory() as data_dir:
            dispatch = Mock(return_value="m3u-task")
            refresh.start(data_dir, 12, 34, [], dispatch)
            self.assertFalse(refresh.start(data_dir, 12, 34, [], dispatch))
            self.assertTrue(refresh.read_state(data_dir)["pending"])
            dispatch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
