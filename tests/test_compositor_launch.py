"""Managed service threads must launch workers without a gevent child watcher."""

import importlib.util
import os
import sys
import threading
import unittest
from unittest.mock import patch

from src import server


@unittest.skipUnless(importlib.util.find_spec("gevent"), "gevent is not installed")
class CompositorLaunchTests(unittest.TestCase):
    def test_worker_pipes_and_wait_in_non_main_thread(self):
        errors = []
        observed = []

        def run():
            try:
                import gevent
                cmd = [sys.executable, "-u", "-c", (
                    "import sys; data=sys.stdin.buffer.readline(); "
                    "sys.stderr.buffer.write(b'notice\\n'); sys.stderr.flush(); "
                    "sys.stdout.buffer.write(data); sys.stdout.flush()"
                )]
                proc = server._start_compositor(cmd, dict(os.environ))
                read_out = gevent.spawn(proc.stdout.readline)
                read_err = gevent.spawn(proc.stderr.readline)
                gevent.sleep(0.01)
                observed.append(not read_out.ready())
                proc.stdin.write(b"hello\n")
                proc.stdin.flush()
                gevent.joinall([read_out, read_err], timeout=5, raise_error=True)
                observed.extend([read_out.value, read_err.value,
                                 gevent.get_hub().threadpool.apply(proc.wait)])
                for pipe in (proc.stdin, proc.stdout, proc.stderr):
                    pipe.close()
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(observed, [True, b"hello\n", b"notice\n", 0])

    def test_refuses_monkey_patched_subprocess(self):
        with patch("gevent.monkey.is_object_patched", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "monkey-patched subprocess"):
                server._start_compositor([], {})


if __name__ == "__main__":
    unittest.main()
