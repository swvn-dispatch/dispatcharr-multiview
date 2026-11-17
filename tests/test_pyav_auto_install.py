"""Consent, durability and locking tests for PyAV installation."""

import io
import hashlib
import os
import tempfile
import threading
import sys
import types
import unittest
import zipfile
from unittest.mock import patch

from src import Plugin, config, deps, server


def wheel_bytes():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(zipfile.ZipInfo("av/__init__.py"), "")
        archive.writestr(zipfile.ZipInfo(f"av-{deps.PYAV_VERSION}.dist-info/METADATA"), "")
    return output.getvalue()


def wheel_info():
    return "https://example.test/wheel", "wheel.whl", hashlib.sha256(wheel_bytes()).hexdigest()


class PyavAutoInstallTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data_dir = self.directory.name
        self.arch = "linux-x86_64"

    @patch.object(deps, "install_pyav")
    @patch.object(deps, "_get_settings", return_value={})
    def test_no_consent_does_not_install(self, settings, install):
        self.assertIn("not authorized", deps.maybe_auto_install(self.arch, self.data_dir)["message"])
        install.assert_not_called()

    def test_current_install_is_distinct_from_missing_consent(self):
        with patch.object(deps, "pyav_status", return_value=deps.PYAV_VERSION), \
             patch.object(deps, "_get_settings", return_value={}):
            self.assertEqual(deps.auto_install_state(self.arch, self.data_dir), "current")
            self.assertIn("already current", deps.maybe_auto_install(self.arch, self.data_dir)["message"])

    def test_detects_supported_linux_platforms_only(self):
        with patch.object(deps.platform, "system", return_value="Linux"):
            with patch.object(deps.platform, "machine", return_value="x86_64"):
                self.assertEqual(deps.detect_arch(), "linux-x86_64")
            with patch.object(deps.platform, "machine", return_value="arm64"):
                self.assertEqual(deps.detect_arch(), "linux-aarch64")
            with patch.object(deps.platform, "machine", return_value="riscv64"):
                self.assertIsNone(deps.detect_arch())
        with patch.object(deps.platform, "system", return_value="Darwin"), patch.object(deps.platform, "machine", return_value="arm64"):
            self.assertIsNone(deps.detect_arch())

    def test_native_fields_check_the_durable_directory(self):
        with patch.object(deps, "detect_arch", return_value=self.arch):
            fields = config.build_plugin_fields({}, self.data_dir)
        self.assertIn("_warn_pyav_missing", [field["id"] for field in fields])

    def test_action_requests_reload_only_after_successful_install(self):
        instance = Plugin()
        context = {"data_dir": self.data_dir}
        for result, expected in [
            ({"status": "success", "message": "installed"}, True),
            ({"status": "error", "message": "failed"}, False),
            ({"status": "partial", "message": "consent failed"}, False),
        ]:
            with self.subTest(result=result), patch.object(instance, "_deps", return_value=deps), \
                 patch.object(deps, "detect_arch", return_value=self.arch), \
                 patch.object(deps, "install_pyav", return_value=result):
                response = instance.run("install_pyav", {}, context)
                self.assertEqual(response.get("_dispatcharr") == {"refresh": "plugin"}, expected)

    def test_automatic_noop_and_revoked_consent_do_not_reload(self):
        instance = Plugin()
        for result in ({"status": "error", "message": "PyAV is already current"},
                       {"status": "error", "message": "Automatic PyAV installation is not authorized"}):
            with self.subTest(result=result), patch.object(instance, "_deps", return_value=deps), \
                 patch.object(deps, "detect_arch", return_value=self.arch), \
                 patch.object(deps, "maybe_auto_install", return_value=result):
                response = instance.run("install_pyav", {"automatic": True}, {"data_dir": self.data_dir})
                self.assertEqual(response["status"], "error")
                self.assertNotIn("_dispatcharr", response)

    def test_successful_automatic_repair_requests_reload(self):
        instance = Plugin()
        with patch.object(instance, "_deps", return_value=deps), \
             patch.object(deps, "detect_arch", return_value=self.arch), \
             patch.object(deps, "maybe_auto_install", return_value={"status": "success"}):
            response = instance.run("install_pyav", {"automatic": True}, {"data_dir": self.data_dir})
        self.assertEqual(response["_dispatcharr"], {"refresh": "plugin"})

    @patch.object(deps, "_save_settings")
    @patch.object(deps, "_find_wheel", return_value=wheel_info())
    def test_install_is_published_in_durable_directory(self, find, save):
        with patch.object(deps.urllib.request, "urlopen", return_value=io.BytesIO(wheel_bytes())):
            result = deps.install_pyav(self.arch, self.data_dir)
        self.assertEqual(result["status"], "success")
        self.assertEqual(deps.pyav_status(self.arch, self.data_dir), deps.PYAV_VERSION)
        self.assertTrue(os.path.isdir(os.path.join(self.data_dir, "vendor", self.arch, deps.PYAV_VERSION, "av")))
        save.assert_called_once_with({f"pyav_consent_{self.arch}": True, "pyav_auto_install_error": None})

    @patch.object(deps, "_save_settings")
    @patch.object(deps, "_find_wheel", return_value=wheel_info())
    def test_incomplete_install_is_preserved_and_replaced(self, find, save):
        incomplete = deps.arch_dir(self.arch, self.data_dir)
        os.makedirs(incomplete)
        with open(os.path.join(incomplete, "partial"), "w") as output:
            output.write("keep")
        with patch.object(deps.urllib.request, "urlopen", return_value=io.BytesIO(wheel_bytes())):
            result = deps.install_pyav(self.arch, self.data_dir)
        self.assertEqual(result["status"], "success")
        vendor = os.path.join(self.data_dir, "vendor")
        preserved = [name for name in os.listdir(vendor) if name.startswith(f".{self.arch}-incomplete-")]
        self.assertEqual(len(preserved), 1)
        self.assertTrue(os.path.isfile(os.path.join(vendor, preserved[0], "partial")))
        self.assertEqual(deps.pyav_status(self.arch, self.data_dir), deps.PYAV_VERSION)

    @patch.object(deps, "_find_wheel")
    @patch.object(deps, "_get_settings", return_value={})
    def test_automatic_install_rechecks_consent_under_lock(self, settings, find):
        result = deps.install_pyav(self.arch, self.data_dir, automatic=True)
        self.assertEqual(result["status"], "error")
        self.assertIn("not authorized", result["message"])
        find.assert_not_called()

    @patch.object(deps, "_save_settings")
    @patch.object(deps, "_find_wheel", return_value=wheel_info())
    def test_failed_download_does_not_publish_partial_install(self, find, save):
        with (
            patch.object(deps.urllib.request, "urlopen", side_effect=OSError("network failure")),
            self.assertLogs(deps.logger, level="ERROR"),
        ):
            result = deps.install_pyav(self.arch, self.data_dir)
        self.assertEqual(result["status"], "error")
        self.assertIsNone(deps.pyav_status(self.arch, self.data_dir))
        save.assert_not_called()

    @patch.object(deps, "_save_settings")
    @patch.object(deps, "_find_wheel", return_value=("https://example.test/wheel", "wheel.whl", "0" * 64))
    def test_checksum_mismatch_never_installs_or_records_consent(self, find, save):
        with (
            patch.object(deps.urllib.request, "urlopen", return_value=io.BytesIO(wheel_bytes())),
            self.assertLogs(deps.logger, level="ERROR"),
        ):
            result = deps.install_pyav(self.arch, self.data_dir)
        self.assertEqual(result["status"], "error")
        self.assertIsNone(deps.pyav_status(self.arch, self.data_dir))
        save.assert_not_called()

    @patch.object(deps, "_save_settings")
    @patch.object(deps, "_get_settings", return_value={"pyav_consent_linux-x86_64": True})
    @patch.object(deps, "_find_wheel", return_value=wheel_info())
    def test_concurrent_repairs_download_once(self, find, settings, save):
        entered = threading.Event()
        release = threading.Event()
        results = []

        def download(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test did not release download")
            return io.BytesIO(wheel_bytes())

        def repair():
            results.append(deps.maybe_auto_install(self.arch, self.data_dir))

        with patch.object(deps.urllib.request, "urlopen", side_effect=download) as urlopen:
            first = threading.Thread(target=repair)
            second = threading.Thread(target=repair)
            first.start()
            try:
                self.assertTrue(entered.wait(5))
                second.start()
            finally:
                release.set()
                first.join(5)
                if second.ident is not None:
                    second.join(5)
            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            urlopen.assert_called_once()
        self.assertEqual(deps.pyav_status(self.arch, self.data_dir), deps.PYAV_VERSION)
        self.assertEqual(len(results), 2)
        self.assertEqual(sorted(result["status"] for result in results), ["error", "success"])
        self.assertTrue(any("already current" in result["message"] for result in results))
        save.assert_called_once()

    def test_stream_worker_receives_durable_vendor_path(self):
        instance = server.MultiviewServer("127.0.0.1", 9292, self.data_dir)
        gevent = types.ModuleType("gevent")
        gevent.spawn = unittest.mock.Mock()
        subprocess = types.ModuleType("gevent.subprocess")
        subprocess.PIPE = object()
        subprocess.Popen = unittest.mock.Mock()
        gevent.subprocess = subprocess
        config = {"out_w": 1920, "out_h": 1080, "fps": 30}
        with (
            patch.dict(sys.modules, {"gevent": gevent, "gevent.subprocess": subprocess}),
            patch.object(instance, "_resolve_layout", return_value=([], "auto", "0")),
            patch.object(instance, "_worker_config", return_value=config),
            patch.object(server, "_settings", return_value={}),
            patch.object(deps, "detect_arch", return_value=self.arch),
            patch.object(deps, "pyav_status", return_value=deps.PYAV_VERSION),
        ):
            instance._serve_stream("layout", unittest.mock.Mock())
        env = subprocess.Popen.call_args.kwargs["env"]
        self.assertEqual(env["MULTIVIEW_VENDOR_DIR"], deps.arch_dir(self.arch, self.data_dir))


if __name__ == "__main__":
    unittest.main()
