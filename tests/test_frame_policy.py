"""Cadence-preserving frame reduction tests."""

import unittest

from src import deps
from src.frame_policy import FrameReduction


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
