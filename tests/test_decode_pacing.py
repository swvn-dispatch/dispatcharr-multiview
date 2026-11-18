"""Simulated bursty decoding and source-clock recovery."""

import unittest

from src.stream_resilience import decode_pacing, presentation_target


def simulate(ahead, source_fps):
    now = 0.0
    arrivals = []
    for i in range(int(source_fps * 10)):
        pts = i / source_fps
        delay, reset = decode_pacing(pts - now, ahead)
        assert not reset
        # A periodic slow scale models jitter, not a source timestamp gap.
        now += delay + (0.065 if i % 12 == 5 else 0.004)
        arrivals.append((now, pts))
    anchor = arrivals[0][0]
    pending = []
    index = skipped = fresh = 0
    for output in range(1, 600):
        wall = anchor + output / 60
        while index < len(arrivals) and arrivals[index][0] <= wall:
            pending.append(arrivals[index][1])
            index += 1
        due = 0
        while pending and pending[0] <= wall - anchor:
            pending.pop(0)
            due += 1
        if due:
            fresh += 1
            skipped += due - 1
    return fresh, skipped, now


class DecodePacingTests(unittest.TestCase):
    def test_decode_ahead_reduces_skips_under_periodic_jitter(self):
        for fps in (30000 / 1001, 60000 / 1001, 30, 60):
            with self.subTest(fps=fps):
                _, without, _ = simulate(0, fps)
                _, with_cushion, _ = simulate(0.15, fps)
                self.assertGreater(without, 0)
                self.assertLess(with_cushion, without)

    def test_cushion_does_not_unbound_source_clock(self):
        _, _, end = simulate(0.15, 30)
        # A long stream can run at most the configured cushion ahead, not
        # continuously accelerate as buffered IPTV input arrives quickly.
        self.assertGreaterEqual(end, 10 - 1 / 30 - 0.15)

    def test_discontinuities_use_uncushioned_source_clock(self):
        self.assertEqual(decode_pacing(2.0), (0.0, True))
        self.assertEqual(decode_pacing(-2.0), (0.0, True))
        self.assertEqual(decode_pacing(0.1), (0.0, False))
        self.assertAlmostEqual(decode_pacing(0.5)[0], 0.35)
        self.assertFalse(decode_pacing(-1.9)[1])

    def test_preroll_absorbs_realtime_source_jitter(self):
        def skipped(fps, preroll):
            arrivals = []
            arrival = 0.0
            for i in range(int(fps * 10)):
                pts = i / fps
                arrival = max(arrival, pts + (0.06 if i % 12 == 5 else 0.0))
                arrivals.append((arrival, pts))
            anchor_pts, anchor_wall, _ = presentation_target(None, None, 0.0, 0.0, preroll)
            index = dropped = 0
            queue = []
            for n in range(600):
                wall = n / 60
                while index < len(arrivals) and arrivals[index][0] <= wall:
                    queue.append(arrivals[index][1])
                    index += 1
                _, _, target = presentation_target(anchor_pts, anchor_wall, 0, wall, preroll)
                due = 0
                while queue and queue[0] <= target:
                    queue.pop(0)
                    due += 1
                dropped += max(0, due - 1)
            return dropped

        for fps in (30000 / 1001, 60000 / 1001, 30, 60):
            with self.subTest(fps=fps):
                self.assertLess(skipped(fps, 0.15), skipped(fps, 0.0))

    def test_preroll_reanchors_after_reset_without_acceleration(self):
        pts, wall, target = presentation_target(None, None, 100.0, 10.0, 0.15)
        self.assertAlmostEqual(target, 99.85)
        self.assertAlmostEqual(wall, 10.15)
        self.assertAlmostEqual(presentation_target(pts, wall, 100.0, 20.15, 0.15)[2], 110.0)
        pts, wall, target = presentation_target(None, None, 5.0, 30.0, 0.15)
        self.assertAlmostEqual(target, 4.85)
        self.assertAlmostEqual(presentation_target(pts, wall, 5.0, 30.15, 0.15)[2], 5.0)


if __name__ == "__main__":
    unittest.main()
