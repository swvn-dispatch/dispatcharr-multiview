"""Tests for PTS-gated compositor audio output."""

import unittest
import threading

from src.audio_policy import samples_before_pts

try:
    import numpy as np
    from src.channel import Channel, AUDIO_RATE
except ModuleNotFoundError:
    np = None
    Channel = None
    AUDIO_RATE = None


class AudioPolicyTests(unittest.TestCase):
    def test_allows_samples_without_pts(self):
        self.assertEqual(samples_before_pts(None, 960, 5.0, 48000), 960)

    def test_holds_audio_that_starts_after_video_limit(self):
        self.assertEqual(samples_before_pts(5.2, 960, 5.1, 48000), 0)

    def test_limits_chunk_at_video_boundary(self):
        self.assertEqual(samples_before_pts(5.0, 960, 5.01, 48000), 480)

    @unittest.skipUnless(np is not None, "compositor runtime dependencies unavailable")
    def test_take_limits_partial_chunk_without_overfilling_output(self):
        track = Channel.__new__(Channel)
        track.alock = threading.Lock()
        track.aframes = [(5.0, np.ones((100, 2), np.int16))]
        track.abuffered = 100
        track.last_taken_pts = None

        pcm = track.take(980, 5.0 + 85 / AUDIO_RATE)

        self.assertTrue(np.all(pcm[:85] == 1))
        self.assertTrue(np.all(pcm[85:] == 0))
        self.assertEqual(track.aframes[0][0], 5.0 + 85 / AUDIO_RATE)
        self.assertEqual(track.aframes[0][1].shape, (15, 2))
        self.assertEqual(track.abuffered, 15)

    @unittest.skipUnless(np is not None, "compositor runtime dependencies unavailable")
    def test_align_trims_the_stale_front_of_a_chunk(self):
        track = Channel.__new__(Channel)
        track.alock = threading.Lock()
        track.aframes = [(5.0, np.ones((100, 2), np.int16))]
        track.abuffered = 100

        track._align_to_pts(5.0 + 85 / AUDIO_RATE)

        self.assertEqual(track.aframes[0][0], 5.0 + 85 / AUDIO_RATE)
        self.assertEqual(track.aframes[0][1].shape, (15, 2))
        self.assertEqual(track.abuffered, 15)


if __name__ == "__main__":
    unittest.main()
