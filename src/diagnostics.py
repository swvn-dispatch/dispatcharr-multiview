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

    def _counts(self):
        return [(c.vcount, c.scaled_vcount, c.reduced_vcount) for c in self.channels]

    def _schedule(self, state, delay):
        timer = self.timer(delay, self._tick, args=(state,))
        timer.daemon = True
        timer.start()

    def start(self, sample_id, duration=120):
        with self.lock:
            if self.active:
                return False
            self.active = True
            started = self.clock()
            state = {"id": sample_id, "started": started, "last": started,
                     "duration": duration, "counts": self._counts(),
                     "totals": [None] * len(self.channels), "intervals": []}
            for channel in self.channels:
                channel.start_presentation_stats()
            self._schedule(state, min(30, duration))
            return True

    @staticmethod
    def _tile(index, name, counts, stats, depth, duration):
        fresh, repeated, skipped, fallback, lag_total, lag_max, lag_count = stats
        return {
            "tile_index": index, "channel_name": name,
            "decoded_fps": round(counts[0] / duration, 2),
            "scaled_fps": round(counts[1] / duration, 2),
            "reduced_frames": counts[2],
            "fresh_frames": fresh, "repeated_frames": repeated,
            "skipped_frames": skipped, "fallback_frames": fallback,
            "queue_depth_at_end": depth,
            "pts_age_mean_ms": round(1000 * lag_total / lag_count, 2) if lag_count else None,
            "pts_age_max_ms": round(1000 * lag_max, 2) if lag_count else None,
        }

    def _tick(self, state):
        result = None
        with self.lock:
            if not self.active:
                return
            now = self.clock()
            elapsed = max(now - state["started"], 0.001)
            interval = max(now - state["last"], 0.001)
            final = elapsed >= state["duration"]
            counts = self._counts()
            tiles = []
            for index, channel in enumerate(self.channels):
                stats, depth = channel.take_presentation_stats(continue_sampling=not final)
                delta = tuple(value - old for value, old in zip(counts[index], state["counts"][index]))
                tiles.append(self._tile(index, channel.name, delta, stats, depth, interval))
                previous = state["totals"][index]
                if previous is None:
                    state["totals"][index] = (delta, stats, depth)
                else:
                    old_counts, old_stats, _ = previous
                    totals = tuple(a + b for a, b in zip(old_counts, delta))
                    combined = tuple(max(a, b) if i == 5 else a + b
                                     for i, (a, b) in enumerate(zip(old_stats, stats)))
                    state["totals"][index] = (totals, combined, depth)
            state["intervals"].append({"elapsed_seconds": round(elapsed, 2),
                                       "duration_seconds": round(interval, 2), "tiles": tiles})
            state["counts"], state["last"] = counts, now
            if final:
                aggregate = [self._tile(i, c.name, *state["totals"][i], elapsed)
                             for i, c in enumerate(self.channels)]
                result = {"sample_id": state["id"], "duration_seconds": round(elapsed, 2),
                          "tiles": aggregate, "intervals": state["intervals"]}
                self.active = False
            else:
                self._schedule(state, min(30, state["duration"] - elapsed))
        if result is not None:
            self.emit(result)
