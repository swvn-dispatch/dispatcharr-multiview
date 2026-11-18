"""Bounded, on-demand presentation sampling inside a compositor worker."""

import threading
import time


class DiagnosticSampler:
    def __init__(self, channels, emit, clock=time.monotonic, timer=threading.Timer):
        self.channels = channels
        self.emit = emit
        self.clock = clock
        self.timer = timer
        self.lock = threading.Lock()
        self.active = False

    def start(self, sample_id, duration=120):
        with self.lock:
            if self.active:
                return False
            self.active = True
            started = self.clock()
            counts = [(c.vcount, c.scaled_vcount, c.reduced_vcount) for c in self.channels]
            for channel in self.channels:
                channel.start_presentation_stats()
            timer = self.timer(duration, self.finish, args=(sample_id, started, counts))
            timer.daemon = True
            timer.start()
            return True

    def finish(self, sample_id, started, counts):
        with self.lock:
            if not self.active:
                return
            duration = max(self.clock() - started, 0.001)
            tiles = []
            for index, (channel, initial) in enumerate(zip(self.channels, counts)):
                (fresh, repeated, skipped, fallback, lag_total, lag_max, lag_count), depth = channel.take_presentation_stats()
                tiles.append({
                    "tile_index": index,
                    "decoded_fps": round((channel.vcount - initial[0]) / duration, 2),
                    "scaled_fps": round((channel.scaled_vcount - initial[1]) / duration, 2),
                    "reduced_frames": channel.reduced_vcount - initial[2],
                    "fresh_frames": fresh, "repeated_frames": repeated,
                    "skipped_frames": skipped, "fallback_frames": fallback,
                    "queue_depth_at_end": depth,
                    "pts_age_mean_ms": round(1000 * lag_total / lag_count, 2) if lag_count else None,
                    "pts_age_max_ms": round(1000 * lag_max, 2) if lag_count else None,
                })
            self.active = False
        self.emit({"sample_id": sample_id, "duration_seconds": round(duration, 2), "tiles": tiles})
