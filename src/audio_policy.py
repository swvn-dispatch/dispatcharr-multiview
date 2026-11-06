"""PTS bounds for the compositor's PCM FIFO."""

import math
import threading


class PresentationClock:
    """Thread-safe source-time boundary for audio released to the encoder."""

    def __init__(self, initial_time):
        self._time = initial_time
        self._lock = threading.Lock()

    def presented_until(self):
        """Return the end time of video successfully accepted by FFmpeg."""
        with self._lock:
            return self._time

    def mark_presented(self, frame_end_time):
        """Advance after the compositor writes the corresponding video frame."""
        with self._lock:
            self._time = frame_end_time


def samples_before_pts(pts, samples, pts_limit, rate):
    """Return how many samples may be emitted without passing *pts_limit*."""
    if pts is None or pts_limit is None:
        return samples
    available = math.floor((pts_limit - pts) * rate + 1e-6)
    return max(0, min(samples, available))
