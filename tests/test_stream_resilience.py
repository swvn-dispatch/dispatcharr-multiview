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

    def test_recovered_video_gets_a_fresh_presentation_anchor(self):
        pts, wall, target = stream_resilience.presentation_target(None, None, 500, 100)
        self.assertEqual((pts, wall, target), (500, 100, 500))

        pts, wall, target = stream_resilience.presentation_target(pts, wall, 501, 100.5)
        self.assertEqual((pts, wall, target), (500, 100, 500.5))
        self.assertEqual(stream_resilience.anchored_pts(pts, wall, 100.5), 500.5)

    def test_presentation_tracks_cadence_change_and_pts_reanchor(self):
        target = stream_resilience.presentation_target
        pts, wall, _ = target(None, None, 100.0, 10.0)
        self.assertAlmostEqual(target(pts, wall, 101.0, 11.0)[2], 101.0)
        # A source can change frame interval while presentation remains tied
        # to timestamps, rather than interpreting frame count as elapsed time.
        source = [100 + i * 1001 / 60000 for i in range(120)]
        source += [102.002 + i * 1001 / 30000 for i in range(60)]
        selected = []
        index = 0
        for n in range(240):
            _, _, due = target(pts, wall, source[index] if index < len(source) else 0, 10 + n / 60)
            count = 0
            while index < len(source) and source[index] <= due + 1e-9:
                index += 1
                count += 1
            selected.append(count)
        self.assertGreater(sum(bool(x) for x in selected[:120]), 115)
        self.assertTrue(58 <= sum(bool(x) for x in selected[120:]) <= 61)
        # A discontinuity clears the anchor and starts the new PTS epoch.
        self.assertTrue(stream_resilience.is_pts_discontinuity(-90))
        pts, wall, due = target(None, None, 12.0, 12.0)
        self.assertEqual((pts, wall, due), (12.0, 12.0, 12.0))

    def test_provider_transition_waits_for_keyframe(self):
        previous = (1280, 720, "yuv420p")
        replacement = (1920, 1080, "yuv420p")
        self.assertTrue(stream_resilience.source_format_changed(previous, replacement))
        self.assertFalse(stream_resilience.source_format_changed(replacement, replacement))
        self.assertFalse(stream_resilience.frame_can_resume(True, False))
        self.assertTrue(stream_resilience.frame_can_resume(True, True))
        self.assertTrue(stream_resilience.frame_can_resume(False, False))

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
