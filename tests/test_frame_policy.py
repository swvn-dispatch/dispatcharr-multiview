"""Cadence-preserving frame reduction tests."""

import unittest

from src import deps
from src.frame_policy import FrameReduction, PresentationStats


class FrameReductionTests(unittest.TestCase):
    def _kept(self, output_fps, source_fps, frames=90):
        policy = FrameReduction(output_fps)
        return [i for i in range(frames) if policy.keep(i / source_fps)]

    def test_reduces_60_fps_source_for_30_fps_output(self):
        kept = self._kept("30", 60)
        self.assertLess(len(kept), 90)
        self.assertGreater(len(kept), 45)

    def test_retains_2997_fps_source_for_5994_fps_output(self):
        kept = self._kept("60000/1001", 30000 / 1001)
        self.assertEqual(kept, list(range(90)))

    def test_missing_pts_is_never_dropped(self):
        policy = FrameReduction("30")
        self.assertTrue(policy.keep(None))

    def test_reset_disables_reduction_until_cadence_is_remeasured(self):
        policy = FrameReduction("30")
        for i in range(40):
            policy.keep(i / 60)
        self.assertTrue(policy.active)
        policy.reset()
        self.assertTrue(policy.keep(0))
        self.assertFalse(policy.active)

    def test_reports_observed_source_cadence(self):
        policy = FrameReduction("30")
        for i in range(10):
            policy.keep(i / 25)
        self.assertAlmostEqual(policy.source_fps(), 25, places=3)

    def test_source_cadence_switch_is_remeasured(self):
        policy = FrameReduction("60")
        for i in range(90):
            self.assertTrue(policy.keep(i * 1001 / 60000))
        self.assertAlmostEqual(policy.source_fps(), 60000 / 1001, places=2)
        start = 90 * 1001 / 60000
        for i in range(90):
            self.assertTrue(policy.keep(start + i * 1001 / 30000))
        self.assertAlmostEqual(policy.source_fps(), 30000 / 1001, places=2)

    def test_presentation_stats_distinguish_repeats_skips_and_fallback(self):
        stats = PresentationStats()
        stats.record(due=1, lag=0.01)
        stats.record()
        stats.record(due=3, lag=0.03)
        stats.record(fallback=True)
        fresh, repeat, skipped, fallback, lag_sum, lag_max, count, details = stats.take()
        self.assertEqual((fresh, repeat, skipped, fallback, count), (2, 1, 2, 1, 2))
        self.assertAlmostEqual(lag_sum, 0.04)
        self.assertAlmostEqual(lag_max, 0.03)
        self.assertEqual(details["depths"], [0, 0, 0, 0])
        self.assertEqual(stats.take()[:7], (0, 0, 0, 0, 0.0, 0.0, 0))


class PyAVWheelTests(unittest.TestCase):
    def test_accepts_cp311_abi3_wheel_for_cp313_runtime(self):
        self.assertTrue(deps._matches_wheel(
            "av-18.1.0-cp311-abi3-manylinux_2_28_x86_64.whl", "linux-x86_64",
        ))

    def test_rejects_wheel_for_other_architecture(self):
        self.assertFalse(deps._matches_wheel(
            "av-18.1.0-cp311-abi3-manylinux_2_28_aarch64.whl", "linux-x86_64",
        ))


if __name__ == "__main__":
    unittest.main()
