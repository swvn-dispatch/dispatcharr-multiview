"""Tests for Dispatcharr live-proxy adapters that need no Django setup."""

import unittest

from src.dispatcharr import RECOVERY_BEHIND_CHUNKS, _LiveEdgeRecoveryBuffer


class _Buffer:
    def __init__(self, index):
        self.index = index
        self.oldest_calls = []

    def find_oldest_available_chunk(self, local_index):
        self.oldest_calls.append(local_index)
        return 12


class LiveEdgeRecoveryBufferTests(unittest.TestCase):
    def test_expired_multiview_cursor_rebases_behind_live_head(self):
        buffer = _Buffer(index=100)

        with self.assertLogs("src.dispatcharr", level="WARNING"):
            index = _LiveEdgeRecoveryBuffer(buffer, "channel").find_oldest_available_chunk(42)

        self.assertEqual(index, 100 - RECOVERY_BEHIND_CHUNKS)
        self.assertEqual(buffer.oldest_calls, [])

    def test_recovery_index_does_not_become_negative(self):
        buffer = _Buffer(index=2)

        with self.assertLogs("src.dispatcharr", level="WARNING"):
            index = _LiveEdgeRecoveryBuffer(buffer, "channel").find_oldest_available_chunk(1)

        self.assertEqual(index, 0)

    def test_nonexpired_cursor_delegates_to_dispatcharr(self):
        buffer = _Buffer(index=42)

        index = _LiveEdgeRecoveryBuffer(buffer, "channel").find_oldest_available_chunk(42)

        self.assertEqual(index, 12)
        self.assertEqual(buffer.oldest_calls, [42])
