import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src import Plugin, control, deps, refresh, storage


class ManifestTests(unittest.TestCase):
    def test_manifest_declares_async_actions_and_capabilities(self):
        manifest = json.loads(Path("src/plugin.json").read_text())
        self.assertEqual(manifest["manifest_version"], 1)
        self.assertEqual(manifest["min_dispatcharr_version"], "v0.34.0")
        self.assertTrue({"background_tasks", "persistent_service", "network_listener", "celery_dispatch", "subprocess", "outbound_network", "proxy_internals", "user_data", "external_dependencies"} <= set(manifest["capabilities"]))
        actions = {action["id"]: action for action in manifest["actions"]}
        self.assertTrue(actions["generate_m3u"]["async"])
        self.assertTrue(actions["install_pyav_amd64"]["async"])
        self.assertEqual(Plugin.actions, manifest["actions"])

    def test_construction_is_inert_and_registers_one_service(self):
        plugin = Plugin()
        context = {"start_service": Mock()}
        plugin.on_leader_acquired(context)
        context["start_service"].assert_called_once()


class StorageMigrationTests(unittest.TestCase):
    def test_available_and_missing_backgrounds(self):
        with tempfile.TemporaryDirectory() as code, tempfile.TemporaryDirectory() as data:
            os.mkdir(os.path.join(code, "style_backgrounds"))
            Path(code, "style_backgrounds", "present.png").write_bytes(b"present")
            settings = {"multiview_custom_layouts": {
                "a": {"name": "A", "background_image": "present.png"},
                "b": {"name": "B", "background_image": "missing.png"},
            }}
            result, changed = storage.migrate_backgrounds(settings, code, data)
            self.assertTrue(changed)
            self.assertEqual(Path(data, "style_backgrounds", "present.png").read_bytes(), b"present")
            self.assertNotIn("background_image", result["multiview_custom_layouts"]["b"])
            self.assertEqual(result["multiview_missing_backgrounds"], ["B"])
            self.assertFalse(storage.migrate_backgrounds(result, code, data)[1])

    def test_storage_error_does_not_clear_references(self):
        with tempfile.TemporaryDirectory() as code, tempfile.TemporaryDirectory() as data:
            settings = {"multiview_custom_layouts": {"x": {"background_image": "a.png"}}}
            with patch("src.storage.os.listdir", side_effect=PermissionError("unavailable")):
                with self.assertRaises(PermissionError):
                    storage.migrate_backgrounds(settings, code, data)
            self.assertEqual(settings["multiview_custom_layouts"]["x"]["background_image"], "a.png")

    def test_control_token_is_private_and_authenticated(self):
        with tempfile.TemporaryDirectory() as data:
            control.ensure_token(data)
            token = Path(data, "control.token").read_text()
            self.assertEqual(os.stat(Path(data, "control.token")).st_mode & 0o777, 0o600)
            self.assertTrue(control.verify_token(data, f"Bearer {token}"))
            self.assertFalse(control.verify_token(data, "Bearer wrong"))

    def test_pyav_install_failure_keeps_existing_version(self):
        with tempfile.TemporaryDirectory() as data:
            destination = Path(data, "vendor", "linux-x86_64", "av")
            destination.mkdir(parents=True)
            with patch.object(deps, "_find_wheel", side_effect=OSError("offline")), self.assertLogs("src.deps", "ERROR"):
                result = deps.install_pyav("linux-x86_64", data)
            self.assertEqual(result["status"], "error")
            self.assertTrue(destination.is_dir())


class RefreshStateTests(unittest.TestCase):
    def test_unknown_dispatch_outcome_never_starts_epg(self):
        with tempfile.TemporaryDirectory() as data:
            refresh.write_state(data, {"phase": "dispatching_m3u", "started": 0})
            dispatch = Mock()
            state = refresh.advance(data, dispatch, Mock(), Mock(), Mock())
            self.assertEqual(state["phase"], "blocked")
            dispatch.assert_not_called()

    def test_inflight_timeout_is_blocked(self):
        with tempfile.TemporaryDirectory() as data:
            refresh.write_state(data, {"phase": "m3u", "started": 0, "task_id": "x"})
            task = Mock()
            task.ready.return_value = False
            state = refresh.advance(data, Mock(), Mock(), lambda _: task, Mock())
            self.assertEqual(state["phase"], "blocked")

    def test_coalesced_refresh_requeues_after_existing_run(self):
        with tempfile.TemporaryDirectory() as data:
            dispatch = Mock(return_value="task")
            refresh.start(data, 1, 2, [], dispatch)
            refresh.start(data, 1, 2, [], dispatch)
            task = Mock(result="Dispatched jobs complete.")
            task.ready.return_value = True
            task.successful.return_value = True
            state = refresh.advance(data, dispatch, Mock(), lambda _: task, lambda *_: None)
            self.assertEqual(state["phase"], "retry_pending")
            queue = Mock()
            refresh.advance(data, dispatch, queue, lambda _: task, lambda *_: None)
            self.assertEqual(queue.call_args.args[0], "generate_m3u")
            self.assertEqual(queue.call_args.args[1]["retry_token"], refresh.read_state(data)["retry_token"])

    def test_unverified_epg_with_pending_work_stays_blocked(self):
        with tempfile.TemporaryDirectory() as data:
            refresh.write_state(data, {"phase": "epg", "task_id": "epg", "started": 1, "pending": True})
            task = Mock()
            task.ready.return_value = True
            task.successful.return_value = True
            dispatch = Mock()
            with patch.object(refresh, "validate_epg", return_value="EPG work may still be running"):
                state = refresh.advance(data, dispatch, Mock(), lambda _: task, Mock())
            self.assertEqual(state["phase"], "blocked")
            dispatch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
