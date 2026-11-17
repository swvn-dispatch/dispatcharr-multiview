"""Regression tests for concurrent path-based plugin imports."""

import importlib
import os
import sys
import tempfile
import threading
import types
import unittest
import uuid
from unittest.mock import patch

import src
from src import server


class StartupModuleLoadingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.name = "test_plugin_" + uuid.uuid4().hex
        self.full_name = f"src.{self.name}"
        self.sync_name = self.name + "_sync"
        self.sync = types.ModuleType(self.sync_name)
        self.sync.entered = threading.Event()
        self.sync.release = threading.Event()
        sys.modules[self.sync_name] = self.sync
        self.addCleanup(sys.modules.pop, self.sync_name, None)
        self.addCleanup(sys.modules.pop, self.full_name, None)
        self.addCleanup(lambda: src.__dict__.pop(self.name, None))
        self.path_patch = patch.object(src, "_PLUGIN_DIR", self.directory.name)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.path_entry = self.directory.name
        src.__path__.append(self.path_entry)
        self.addCleanup(src.__path__.remove, self.path_entry)

    def write_module(self, body):
        with open(os.path.join(self.directory.name, self.name + ".py"), "w") as f:
            f.write(body)
        importlib.invalidate_caches()

    def test_concurrent_loads_wait_for_initialization(self):
        self.write_module(
            f"import {self.sync_name} as sync\n"
            "sync.entered.set()\nsync.release.wait(5)\nready = True\n"
        )
        # A second package object with the same name models a Dispatcharr reload.
        reloaded_loader = types.FunctionType(
            src._load_submodule.__code__, dict(src._load_submodule.__globals__)
        )
        for first, second in (
            (lambda: src._load_submodule(self.name), lambda: importlib.import_module(self.full_name)),
            (lambda: importlib.import_module(self.full_name), lambda: reloaded_loader(self.name)),
            (lambda: src._load_submodule(self.name), lambda: reloaded_loader(self.name)),
        ):
            with self.subTest(first=first, second=second):
                sys.modules.pop(self.full_name, None)
                src.__dict__.pop(self.name, None)
                self.sync.entered.clear()
                self.sync.release.clear()
                results = []
                errors = []
                second_started = threading.Event()
                second_finished = threading.Event()

                def run(loader, started=None, finished=None):
                    try:
                        if started:
                            started.set()
                        results.append(loader())
                    except BaseException as exc:
                        errors.append(exc)
                    finally:
                        if finished:
                            finished.set()

                t1 = threading.Thread(target=run, args=(first,))
                t2 = threading.Thread(target=run, args=(second, second_started, second_finished))
                t1.start()
                try:
                    self.assertTrue(self.sync.entered.wait(2))
                    t2.start()
                    self.assertTrue(second_started.wait(2))
                    self.assertFalse(second_finished.wait(0.1))
                finally:
                    self.sync.release.set()
                    t1.join(5)
                    if t2.ident is not None:
                        t2.join(5)
                self.assertFalse(t1.is_alive())
                self.assertFalse(t2.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(len(results), 2)
                self.assertIs(results[0], results[1])
                self.assertTrue(results[0].ready)
                self.assertIs(src._load_submodule(self.name), results[0])

    def test_failed_load_is_removed_and_can_be_retried(self):
        self.write_module(
            f"import {self.sync_name} as sync\n"
            "if not hasattr(sync, 'attempted'):\n"
            "    sync.attempted = True\n"
            "    raise RuntimeError('first attempt')\n"
            "ready = True\n"
        )
        with self.assertRaisesRegex(RuntimeError, "first attempt"):
            src._load_submodule(self.name)
        self.assertNotIn(self.full_name, sys.modules)
        self.assertTrue(src._load_submodule(self.name).ready)

    def test_parent_package_may_be_missing(self):
        self.write_module("ready = True\n")
        with patch.dict(sys.modules):
            del sys.modules["src"]
            self.assertTrue(src._load_submodule(self.name).ready)

    def test_path_loader_inherits_host_builtins(self):
        import builtins
        self.write_module("observed = host_guard_marker\n")
        guarded = dict(vars(builtins), host_guard_marker="guarded")
        with patch.object(src, "__builtins__", guarded):
            module = src._load_submodule(self.name)
        self.assertEqual(module.observed, "guarded")
        self.assertIs(module.__builtins__, guarded)

    def test_dashboard_path_loader_inherits_host_builtins(self):
        import builtins
        guarded = dict(vars(builtins))
        with (
            patch.dict(sys.modules),
            patch.object(server, "_dash_api", None),
            patch.object(server, "__builtins__", guarded),
        ):
            sys.modules.pop("src.dash_api", None)
            module = server._load_dash_api()
        self.assertIs(module.__builtins__, guarded)


if __name__ == "__main__":
    unittest.main()
