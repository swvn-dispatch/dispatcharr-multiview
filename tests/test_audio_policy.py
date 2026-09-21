"""Tests for PTS-gated compositor audio output."""

import unittest

from src.audio_policy import samples_before_pts


class AudioPolicyTests(unittest.TestCase):
    def test_allows_samples_without_pts(self):
        self.assertEqual(samples_before_pts(None, 960, 5.0, 48000), 960)

    def test_holds_audio_that_starts_after_video_limit(self):
        self.assertEqual(samples_before_pts(5.2, 960, 5.1, 48000), 0)

    def test_limits_chunk_at_video_boundary(self):
        self.assertEqual(samples_before_pts(5.0, 960, 5.01, 48000), 480)


if __name__ == "__main__":
    unittest.main()
