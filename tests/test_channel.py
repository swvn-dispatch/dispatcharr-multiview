"""Regression tests for per-tile decoder recovery helpers."""

import unittest
from unittest.mock import patch

try:
    from src import channel
except ModuleNotFoundError:
    channel = None


class _Resampler:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.closed = False

    def resample(self, frame):
        if self.error:
            raise self.error
        return self.result

    def close(self):
        self.closed = True


class _Packet:
    def __init__(self, error=None):
        self.error = error

    def decode(self):
        if self.error:
            raise self.error
        return []


@unittest.skipIf(channel is None, "channel decoder dependencies are not installed")
class AudioResamplerRecoveryTests(unittest.TestCase):
    def test_format_change_replaces_resampler_without_raising(self):
        original = _Resampler(error=ValueError("format changed"))
        replacement = _Resampler(result=["audio"])

        with patch.object(channel.av, "AudioResampler", return_value=replacement):
            frames, resampler = channel.resample_audio_frame(original, object(), "Test")

        self.assertEqual(frames, ["audio"])
        self.assertIs(resampler, replacement)
        self.assertTrue(original.closed)

    def test_incompatible_frame_is_dropped_after_resampler_reset(self):
        original = _Resampler(error=ValueError("format changed"))
        replacement = _Resampler(error=ValueError("unsupported"))

        with patch.object(channel.av, "AudioResampler", return_value=replacement):
            frames, resampler = channel.resample_audio_frame(original, object(), "Test")

        self.assertEqual(frames, ())
        self.assertIs(resampler, replacement)

    def test_bad_audio_packet_is_skipped(self):
        self.assertEqual(
            channel.decode_audio_packet(_Packet(ValueError("corrupt")), "Test"),
            (),
        )
