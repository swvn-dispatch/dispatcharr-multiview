"""TilePipeline: one always-on normalized tile stream for the composition.

A tile is served to the composition ffmpeg as a single continuous MPEG-TS HTTP
response that internally switches sources without ever breaking:

    logo placeholder  --splice@keyframe-->  live channel  --on drop-->  offline card

The placeholder and live encoders emit byte-identical stream parameters and PIDs
(see ffmpeg.py), so splicing is just: keep one ContinuityRewriter across the
whole output, switch the byte source at a live keyframe, and flag that first
keyframe packet with the discontinuity indicator. The composition timestamps the
tile with wallclock, so the PCR/PTS jump at the splice is ignored.

Everything runs under gevent (Dispatcharr monkey-patches at startup): ffmpeg via
gevent.subprocess, pipe reads are cooperative, and the live encoder is drained by
a warm-live greenlet that hands packets to the response greenlet through a
bounded gevent Queue. No OS threads, no cross-thread queues.

v1 drop behavior: when the live source ends, the tile shows an offline card and
stays there. Re-splicing back to live on recovery is tracked separately
(see the reconnect bead).
"""

import logging

from . import ffmpeg as _ffmpeg
from . import tsutil

logger = logging.getLogger(__name__)

READ_SIZE = 188 * 360       # ~67 KB, packet-aligned read size
BATCH_PACKETS = 360         # queue drain batch -> one socket write
LIVE_QUEUE_MAX = 512        # bounded; backpressures the live encoder via its pipe


def _kill_proc(proc) -> None:
    if proc is None:
        return
    try:
        proc.kill()
        proc.wait()
    except Exception:
        pass


class TilePipeline:
    def __init__(self, settings: dict, channel_id, realsrc_url: str,
                 logo_path: "str | None", name: str = ""):
        self.settings = settings
        self.channel_id = channel_id
        self.realsrc_url = realsrc_url
        self.logo_path = logo_path
        self.name = name or str(channel_id)

        self._procs = []          # all ffmpeg procs, killed on teardown
        self._greenlets = []
        self.placeholder_proc = None
        self.live_proc = None
        self._live_q = None
        self.live_ready = None
        self.live_failed = None

    # ------------------------------------------------------------------ public

    def stream(self):
        """Generator yielding the continuous tile MPEG-TS for the HTTP response."""
        import gevent
        from gevent.event import Event
        from gevent.queue import Queue

        self._live_q = Queue(maxsize=LIVE_QUEUE_MAX)
        self.live_ready = Event()
        self.live_failed = Event()
        rw = tsutil.ContinuityRewriter()

        try:
            self.placeholder_proc = self._spawn_placeholder(bg="black")
            warm = gevent.spawn(self._warm_live)
            self._greenlets.append(warm)

            # Phase 1: placeholder (logo) until the live source has a keyframe.
            yield from self._pump_placeholder(self.placeholder_proc, rw,
                                              stop=self.live_ready)

            if not self.live_ready.is_set():
                # placeholder pump only returns early on stop; if we got here
                # without live_ready the consumer is gone.
                return

            # Splice point: drop the placeholder, switch to live.
            _kill_proc(self.placeholder_proc)
            self.placeholder_proc = None
            live_ended = yield from self._pump_live_queue(rw)

            # Phase 3 (v1): live ended -> offline card, stay put.
            if live_ended:
                offline = self._spawn_placeholder(bg="#400000")  # dark red = offline
                self.placeholder_proc = offline
                yield from self._pump_placeholder(offline, rw, stop=None)
        except GeneratorExit:
            pass
        finally:
            self._teardown()

    # ---------------------------------------------------------------- internal

    def _spawn_placeholder(self, bg: str = "black"):
        import gevent
        import gevent.subprocess as gsub
        cmd = _ffmpeg.build_placeholder_cmd(self.settings, self.logo_path, bg)
        proc = gsub.Popen(cmd, stdout=gsub.PIPE, stderr=gsub.PIPE)
        self._procs.append(proc)
        self._greenlets.append(gevent.spawn(self._drain_stderr, proc, f"placeholder-{self.name}"))
        return proc

    def _pump_placeholder(self, proc, rw, stop):
        """Yield rewritten placeholder packets until `stop` is set (or forever if
        None). Respawns the placeholder if it dies unexpectedly."""
        leftover = b""
        while stop is None or not stop.is_set():
            data = proc.stdout.read(READ_SIZE)
            if not data:
                # placeholder ffmpeg died; restart it so the tile keeps emitting
                logger.warning(f"multiview: placeholder for {self.name} ended, restarting")
                _kill_proc(proc)
                proc = self._spawn_placeholder(bg="black")
                self.placeholder_proc = proc
                leftover = b""
                continue
            pkts, leftover = tsutil.split_packets(leftover + data)
            if pkts:
                yield b"".join(rw.process(p) for p in pkts)

    def _pump_live_queue(self, rw):
        """Drain the live packet queue to the response. Returns True if the live
        source ended (sentinel reached), False on consumer disconnect."""
        from gevent.queue import Empty
        first = True
        while True:
            pkt = self._live_q.get()
            if pkt is None:
                return True
            batch = [pkt]
            try:
                while len(batch) < BATCH_PACKETS:
                    batch.append(self._live_q.get_nowait())
            except Empty:
                pass

            ended = False
            if batch and batch[-1] is None:
                batch.pop()
                ended = True

            out = []
            for p in batch:
                if first:
                    p = tsutil.set_discontinuity(p)
                    first = False
                out.append(rw.process(p))
            if out:
                yield b"".join(out)
            if ended:
                return True

    def _warm_live(self):
        """Read the live encoder, discard until the first keyframe, then feed all
        packets into the queue. Sole reader of the live pipe for the tile's life."""
        import gevent.subprocess as gsub
        import gevent
        try:
            cmd = _ffmpeg.build_live_tile_cmd(self.settings, self.realsrc_url)
            self.live_proc = gsub.Popen(cmd, stdout=gsub.PIPE, stderr=gsub.PIPE)
            self._procs.append(self.live_proc)
            self._greenlets.append(gevent.spawn(self._drain_stderr, self.live_proc, f"live-{self.name}"))

            leftover = b""
            found_kf = False
            while True:
                data = self.live_proc.stdout.read(READ_SIZE)
                if not data:
                    break
                pkts, leftover = tsutil.split_packets(leftover + data)
                for p in pkts:
                    if not found_kf:
                        if tsutil.is_random_access(p, _ffmpeg.VIDEO_PID):
                            found_kf = True
                            self._live_q.put(p)
                            self.live_ready.set()
                            logger.info(f"multiview: tile {self.name} live keyframe, splicing")
                        # else: discard pre-keyframe packets
                    else:
                        self._live_q.put(p)  # cooperative block if full
        except Exception as e:  # noqa: BLE001
            logger.warning(f"multiview: live warm for {self.name} failed: {e}")
        finally:
            try:
                self._live_q.put(None)  # sentinel: live ended
            except Exception:
                pass
            if not self.live_ready.is_set():
                self.live_failed.set()

    def _drain_stderr(self, proc, label):
        try:
            for raw in proc.stderr:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    logger.warning(f"ffmpeg {label}: {line}")
        except Exception:
            pass

    def _teardown(self):
        for g in self._greenlets:
            try:
                g.kill(block=False)
            except Exception:
                pass
        for proc in self._procs:
            _kill_proc(proc)
        logger.info(f"multiview: tile {self.name} torn down")
