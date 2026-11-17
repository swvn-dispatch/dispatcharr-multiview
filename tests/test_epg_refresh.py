"""Regression tests for multiview's generated EPG refresh contract."""

import unittest
from unittest.mock import Mock

from src import _m3u_content
from src import epg


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


if __name__ == "__main__":
    unittest.main()
