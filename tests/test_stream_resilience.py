"""Source-transition contract tests that run without PyAV or numpy."""

import unittest

from src import stream_resilience


class _Packet:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def decode(self):
        if self.error:
            raise self.error
        return self.result


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


class StreamTransitionTests(unittest.TestCase):
    def test_hold_retains_last_frame_until_recovery_window_expires(self):
        self.assertEqual(stream_resilience.held_frame("last", "logo", 115, 114.9), "last")
        self.assertEqual(stream_resilience.held_frame("last", "logo", 115, 115), "logo")

    def test_pts_resets_and_forward_jumps_are_recoverable(self):
        self.assertTrue(stream_resilience.is_pts_progress(100, 101))
        self.assertTrue(stream_resilience.is_pts_progress(100, 97))
        self.assertTrue(stream_resilience.is_pts_discontinuity(3))
        self.assertTrue(stream_resilience.is_pts_discontinuity(-3))

    def test_bad_transition_packet_is_dropped_without_raising(self):
        errors = []
        frames = stream_resilience.decode_packet(
            _Packet(error=ValueError("corrupt")), errors.append,
        )

        self.assertEqual(frames, ())
        self.assertEqual(len(errors), 1)

    def test_audio_format_change_recreates_only_the_resampler(self):
        original = _Resampler(error=ValueError("format changed"))
        replacement = _Resampler(result=["audio"])
        errors = []

        frames, active = stream_resilience.resample_frame(
            original, object(), lambda: replacement,
            lambda action, error: errors.append((action, str(error))),
        )

        self.assertEqual(frames, ["audio"])
        self.assertIs(active, replacement)
        self.assertTrue(original.closed)
        self.assertEqual(errors, [("reset", "format changed")])

    def test_incompatible_audio_frame_is_dropped_after_reset(self):
        replacement = _Resampler(error=ValueError("unsupported"))
        errors = []

        frames, active = stream_resilience.resample_frame(
            _Resampler(error=ValueError("format changed")), object(), lambda: replacement,
            lambda action, error: errors.append((action, str(error))),
        )

        self.assertEqual(frames, ())
        self.assertIs(active, replacement)
        self.assertEqual(errors, [("reset", "format changed"), ("drop", "unsupported")])
