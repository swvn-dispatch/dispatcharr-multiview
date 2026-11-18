"""Bounded, on-demand presentation sampling inside a compositor worker."""

import threading
import time


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[round((len(ordered) - 1) * fraction)] * 1000, 2)


class OutputMeter:
    """Collect encoder pipe write time only while a sample is active."""

    def __init__(self):
        self.lock = threading.Lock()
        self.active = False
        self.frames = 0
        self.write_seconds = 0.0

    def start(self):
        with self.lock:
            self.active = True
            self.frames = 0
            self.write_seconds = 0.0

    def record(self, seconds):
        if self.active:
            with self.lock:
                if self.active:
                    self.frames += 1
                    self.write_seconds += seconds

    def take(self, final=False):
        with self.lock:
            result = (self.frames, self.write_seconds)
            self.frames = 0
            self.write_seconds = 0.0
            if final:
                self.active = False
            return result


class DiagnosticSampler:
    def __init__(self, channels, emit, clock=time.monotonic, timer=threading.Timer, output_meter=None, output_info=None):
        self.channels = channels
        self.emit = emit
        self.clock = clock
        self.timer = timer
        self.output_meter = output_meter
        self.output_info = output_info
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
                     "totals": [None] * len(self.channels), "intervals": [],
                     "output_frames": 0, "output_write_seconds": 0.0}
            for channel in self.channels:
                channel.start_presentation_stats()
            if self.output_meter:
                self.output_meter.start()
            self._schedule(state, min(1, duration))
            return True

    @staticmethod
    def _tile(index, name, counts, stats, depth, duration, started):
        fresh, repeated, skipped, fallback, lag_total, lag_max, lag_count, details = stats
        depths, ages, leads = details["depths"], details["ages"], details["leads"]
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
            "pts_age_p95_ms": _percentile(ages, 0.95),
            "queue_depth_min": min(depths) if depths else None,
            "queue_depth_mean": round(sum(depths) / len(depths), 2) if depths else None,
            "queue_depth_max": max(depths) if depths else None,
            "queue_empty_percent": round(100 * depths.count(0) / len(depths), 2) if depths else None,
            "enqueue_lead_p50_ms": _percentile(leads, 0.5),
            "enqueue_lead_p05_ms": _percentile(leads, 0.05),
            "enqueue_lead_p95_ms": _percentile(leads, 0.95),
            "enqueue_late_percent": round(100 * sum(value < 0 for value in leads) / len(leads), 2) if leads else None,
            "events": [{"type": kind, "elapsed_seconds": round(max(0, timestamp - started), 2)}
                       for kind, timestamp in details["events"]],
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
                tile = self._tile(index, channel.name, delta, stats, depth, interval, state["started"])
                tile.update(channel.diagnostic_source_info())
                tiles.append(tile)
                previous = state["totals"][index]
                if previous is None:
                    state["totals"][index] = (delta, stats, depth)
                else:
                    old_counts, old_stats, _ = previous
                    totals = tuple(a + b for a, b in zip(old_counts, delta))
                    combined = tuple(max(a, b) if i == 5 else a + b
                                     for i, (a, b) in enumerate(zip(old_stats[:7], stats[:7])))
                    combined += ({key: old_stats[7][key] + stats[7][key] for key in old_stats[7]},)
                    state["totals"][index] = (totals, combined, depth)
            output = None
            if self.output_meter:
                frames, write_seconds = self.output_meter.take(final=final)
                state["output_frames"] += frames
                state["output_write_seconds"] += write_seconds
                output = {"fps": round(frames / interval, 2),
                          "encoder_pipe_write_ms": round(1000 * write_seconds, 2)}
            state["intervals"].append({"elapsed_seconds": round(elapsed, 2),
                                       "duration_seconds": round(interval, 2), "tiles": tiles,
                                       "output": output})
            state["counts"], state["last"] = counts, now
            if final:
                aggregate = [self._tile(i, c.name, *state["totals"][i], elapsed, state["started"])
                             for i, c in enumerate(self.channels)]
                for tile, channel in zip(aggregate, self.channels):
                    tile.update(channel.diagnostic_source_info())
                result = {"sample_id": state["id"], "duration_seconds": round(elapsed, 2),
                          "tiles": aggregate, "intervals": state["intervals"],
                          "output_configuration": self.output_info,
                          "output": {"fps": round(state["output_frames"] / elapsed, 2),
                                     "encoder_pipe_write_ms": round(1000 * state["output_write_seconds"], 2)} if self.output_meter else None}
                self.active = False
            else:
                self._schedule(state, min(1, state["duration"] - elapsed))
        if result is not None:
            self.emit(result)
