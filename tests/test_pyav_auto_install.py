"""Consent and locking tests for background PyAV repair."""

import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from src import deps


class PyavAutoInstallTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.vendor = patch.object(deps, "VENDOR_DIR", self.tempdir.name)
        self.vendor.start()
        self.addCleanup(self.vendor.stop)
        self.arch = "linux-x86_64"
        self.lock = os.path.join(self.tempdir.name, f".autoinstall_{self.arch}.lock")

    @patch.object(deps, "install_pyav")
    @patch.object(deps, "_get_settings", return_value={})
    def test_no_consent_does_not_install(self, settings, install):
        self.assertIsNone(deps.maybe_auto_install(self.arch))
        install.assert_not_called()
        self.assertFalse(os.path.exists(self.lock))

    @patch.object(deps, "install_pyav")
    @patch.object(deps, "pyav_status", return_value=deps.PYAV_VERSION)
    @patch.object(deps, "_get_settings", return_value={"pyav_consent_linux-x86_64": True})
    def test_current_install_does_not_lock_or_install(self, settings, status, install):
        self.assertIsNone(deps.maybe_auto_install(self.arch))
        install.assert_not_called()
        self.assertFalse(os.path.exists(self.lock))

    @patch.object(deps, "install_pyav")
    @patch.object(deps, "pyav_status", return_value=None)
    @patch.object(deps, "_get_settings", return_value={"pyav_consent_linux-x86_64": True})
    def test_existing_lock_is_not_removed(self, settings, status, install):
        with open(self.lock, "w"):
            pass
        self.assertIsNone(deps.maybe_auto_install(self.arch))
        install.assert_not_called()
        self.assertTrue(os.path.exists(self.lock))

    @patch.object(deps, "_save_settings")
    @patch.object(deps, "pyav_status", return_value=None)
    @patch.object(deps, "_get_settings", return_value={"pyav_consent_linux-x86_64": True})
    def test_failed_install_records_error_and_releases_lock(self, settings, status, save):
        result = {"status": "error", "message": "download failed"}

        def install(arch):
            self.assertEqual(arch, self.arch)
            self.assertTrue(os.path.exists(self.lock))
            return result

        with patch.object(deps, "install_pyav", side_effect=install):
            self.assertEqual(deps.maybe_auto_install(self.arch), result)
        save.assert_called_once_with({"pyav_auto_install_error": "download failed"})
        self.assertFalse(os.path.exists(self.lock))

    @patch.object(deps, "pyav_status", return_value=None)
    @patch.object(deps, "_get_settings", return_value={"pyav_consent_linux-x86_64": True})
    def test_concurrent_repairs_only_install_once(self, settings, status):
        entered = threading.Event()
        release = threading.Event()
        result = {"status": "success"}

        def install(arch):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test did not release installer")
            return result

        with patch.object(deps, "install_pyav", side_effect=install) as installer:
            worker = threading.Thread(target=deps.maybe_auto_install, args=(self.arch,))
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assertIsNone(deps.maybe_auto_install(self.arch))
            finally:
                release.set()
                worker.join(5)
            self.assertFalse(worker.is_alive())
            installer.assert_called_once_with(self.arch)
        self.assertFalse(os.path.exists(self.lock))


if __name__ == "__main__":
    unittest.main()
