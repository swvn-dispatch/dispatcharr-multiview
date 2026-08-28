"""Regression tests for multiview's generated EPG refresh contract."""

import sys
import types
import unittest
from unittest.mock import Mock, patch

from src import Plugin
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


class RefreshOrderTests(unittest.TestCase):
    def test_m3u_refresh_precedes_epg_refresh(self):
        m3u_task = Mock()
        epg_task = Mock()
        m3u_signature = object()
        epg_signature = object()
        m3u_task.si.return_value = m3u_signature
        epg_task.si.return_value = epg_signature
        chained = Mock()
        chain = Mock(return_value=chained)

        celery = types.ModuleType("celery")
        celery.chain = chain
        apps = types.ModuleType("apps")
        m3u = types.ModuleType("apps.m3u")
        m3u_tasks = types.ModuleType("apps.m3u.tasks")
        m3u_tasks.refresh_single_m3u_account = m3u_task
        epg_package = types.ModuleType("apps.epg")
        epg_tasks = types.ModuleType("apps.epg.tasks")
        epg_tasks.refresh_epg_data = epg_task

        modules = {
            "celery": celery,
            "apps": apps,
            "apps.m3u": m3u,
            "apps.m3u.tasks": m3u_tasks,
            "apps.epg": epg_package,
            "apps.epg.tasks": epg_tasks,
        }
        with patch.dict(sys.modules, modules):
            Plugin.__new__(Plugin)._refresh_m3u_then_epg(12, 34)

        m3u_task.si.assert_called_once_with(12)
        epg_task.si.assert_called_once_with(34)
        chain.assert_called_once_with(m3u_signature, epg_signature)
        chained.delay.assert_called_once_with()

    def test_m3u_refresh_runs_without_epg_source(self):
        m3u_task = Mock()
        celery = types.ModuleType("celery")
        celery.chain = Mock()
        apps = types.ModuleType("apps")
        m3u = types.ModuleType("apps.m3u")
        m3u_tasks = types.ModuleType("apps.m3u.tasks")
        m3u_tasks.refresh_single_m3u_account = m3u_task

        modules = {
            "celery": celery,
            "apps": apps,
            "apps.m3u": m3u,
            "apps.m3u.tasks": m3u_tasks,
        }
        with patch.dict(sys.modules, modules):
            Plugin.__new__(Plugin)._refresh_m3u_then_epg(12, None)

        m3u_task.delay.assert_called_once_with(12)


if __name__ == "__main__":
    unittest.main()
