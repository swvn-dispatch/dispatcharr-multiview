"""Channel decoder: one child IPTV stream demuxed into video and audio buffers.

Imported by compositor_worker.py (the subprocess entry point). Lives here so
the PyAV/numpy dependency and the YUV compositing utilities are co-located with
the class that uses them, separate from the encoder and orchestration code.
"""

import collections
import os
import platform
import sys
import threading
import time

try:
    from .audio_policy import samples_before_pts
    from .frame_policy import FrameReduction
    from .stream_resilience import (
        PTS_DISCONTINUITY_SECS, decode_packet, held_frame,
        is_pts_discontinuity, is_pts_progress, presentation_target, anchored_pts, resample_frame,
        frame_can_resume, source_format_changed,
    )
except ImportError:
    from audio_policy import samples_before_pts
    from frame_policy import FrameReduction
    from stream_resilience import (
        PTS_DISCONTINUITY_SECS, decode_packet, held_frame,
        is_pts_discontinuity, is_pts_progress, presentation_target, anchored_pts, resample_frame,
        frame_can_resume, source_format_changed,
    )

# Vendored PyAV is shipped per-platform under vendor/<os-arch>/; pick the one
# matching this machine and put it on the path before importing av.
_VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
_ARCH_DIR = {
    "x86_64": "linux-x86_64", "amd64": "linux-x86_64",
    "aarch64": "linux-aarch64", "arm64": "linux-aarch64",
}.get(platform.machine().lower())
if _ARCH_DIR and os.path.isdir(os.path.join(_VENDOR, _ARCH_DIR)):
    sys.path.insert(0, os.path.join(_VENDOR, _ARCH_DIR))

import numpy as np  # noqa: E402
try:
    import av  # noqa: E402  (vendored, installed on demand)
except ImportError:
    sys.stderr.write(
        f"[mvworker] FATAL: PyAV not installed for arch '{platform.machine()}' "
        f"(expected {_VENDOR}/{_ARCH_DIR}). Open the Multiview plugin settings and "
        f"run the 'Install PyAV' action.\n")
    raise

TILE_STALE_SECS = 1.5
VIDEO_PTS_STALL_SECS = 10.0
RECONNECT_BASE    = 2.0   # first retry delay (seconds)
RECONNECT_MAX     = 60.0  # cap on per-retry delay
RECONNECT_RETRIES = 12    # consecutive failures before giving up (~8 min total)
AUDIO_RATE = 48000
AUDIO_LAYOUT = "stereo"
VIDEO_QUEUE_FRAMES = 120
AUDIO_RECOVERY_FADE_SECS = 0.15

# Tolerate flaky IPTV (skip corrupt packets, ignore decode errors, generous
# probe) and bound I/O so a dead child errors and retries instead of hanging.
# Matches what the old ffmpeg tile decoders used; PyAV's strict defaults choke
# on partial/corrupt mpegts ("Invalid data found when processing input").
DECODE_OPTS = {
    "fflags": "+discardcorrupt+genpts",
    "analyzeduration": "5000000",
    "probesize": "5000000",
    "err_detect": "ignore_err",
    "rw_timeout": "15000000",   # 15s I/O timeout (microseconds)
}


def log(msg):
    sys.stderr.write(f"[mvworker] {msg}\n")
    sys.stderr.flush()


def yuv_planes_from_frame(frame, w, h):
    """Extract (Y, U, V) as contiguous numpy arrays from a yuv420p VideoFrame,
    stripping each plane's stride padding."""
    p0, p1, p2 = frame.planes
    Y = np.frombuffer(memoryview(p0), np.uint8).reshape(h, p0.line_size)[:, :w]
    U = np.frombuffer(memoryview(p1), np.uint8).reshape(h // 2, p1.line_size)[:, :w // 2]
    V = np.frombuffer(memoryview(p2), np.uint8).reshape(h // 2, p2.line_size)[:, :w // 2]
    return Y.copy(), U.copy(), V.copy()


def black_planes(w, h):
    return (np.zeros((h, w), np.uint8),
            np.full((h // 2, w // 2), 128, np.uint8),
            np.full((h // 2, w // 2), 128, np.uint8))


def resample_audio_frame(resampler, frame, channel_name):
    """Resample one frame, recreating state when an upstream format changes.

    A live-proxy stream can switch providers with a different audio layout or
    sample rate. PyAV resamplers are tied to their first input format, so that
    transition must not tear down the tile's video connection.
    """
    try:
        return resampler.resample(frame), resampler
    except Exception as e:  # noqa: BLE001
        log(f"channel {channel_name}: audio format changed; resetting resampler: {e}")
        try:
            resampler.close()
        except Exception:
            pass
        replacement = av.AudioResampler(format="s16", layout=AUDIO_LAYOUT, rate=AUDIO_RATE)
        try:
            return replacement.resample(frame), replacement
        except Exception as retry_error:  # noqa: BLE001
            log(f"channel {channel_name}: dropping incompatible audio frame: {retry_error}")
            return (), replacement


def decode_audio_packet(packet, channel_name):
    """Decode an audio packet without letting a transition end the tile."""
    try:
        return packet.decode()
    except Exception as e:  # noqa: BLE001
        # Provider switches can leave one corrupt or format-transition audio
        # packet in flight. Skip it without interrupting the selected tile's
        # video or subsequent audio.
        log(f"channel {channel_name}: dropping bad audio packet: {e}")
        return ()


def _yuv_planes(buf, w, h):
    """(Y, U, V) plane views into a flat yuv420p buffer (Y|U|V byte order)."""
    ysize = w * h
    csize = (w // 2) * (h // 2)
    Y = buf[:ysize].reshape(h, w)
    U = buf[ysize:ysize + csize].reshape(h // 2, w // 2)
    V = buf[ysize + csize:ysize + 2 * csize].reshape(h // 2, w // 2)
    return Y, U, V


def _even(v):
    return max(2, (int(v) // 2) * 2)


def fit_into_tile(frame, w, h, valign="center", halign="center"):
    """Scale a decoded frame preserving aspect ratio (content is never
    cropped). Letterbox/pillarbox padding is centered by default, or pushed
    to one edge via valign ("center"/"top"/"bottom") and halign
    ("center"/"left"/"right") so a tile's content can be made to touch an
    adjacent tile's content with no gap at the shared edge, leaving any
    padding on the outer edge instead.

    Returns (Y, U, V, ox, oy, tw, th) -- the content-sized planes and their
    placement within the w x h tile -- rather than a full w x h buffer with
    black padding baked in, so the compositor can blit only the real content
    and leave letterbox/pillarbox padding alone (showing whatever's already
    on the persistent canvas, e.g. the background image, instead of painting
    opaque black over it every frame).
    """
    sw, sh = frame.width, frame.height
    if sw <= 0 or sh <= 0:
        return (*black_planes(w, h), 0, 0, w, h)
    scale = min(w / sw, h / sh)
    tw, th = _even(sw * scale), _even(sh * scale)
    tw, th = min(tw, w), min(th, h)
    sf = frame.reformat(width=tw, height=th, format="yuv420p")
    sy, su, sv = yuv_planes_from_frame(sf, tw, th)
    if halign == "left":
        ox = 0
    elif halign == "right":
        ox = (w - tw) & ~1
    else:
        ox = ((w - tw) // 2) & ~1
    if valign == "top":
        oy = 0
    elif valign == "bottom":
        oy = (h - th) & ~1
    else:
        oy = ((h - th) // 2) & ~1
    return (sy, su, sv, ox, oy, tw, th)


class Channel:
    """One child channel: ONE realsrc connection, demuxed into this tile's video
    and (if this channel supplies audio) its audio track. Decoding each channel
    once (instead of separate video+audio connections) halves the load on the
    provider/proxy, which was corrupting the video under multiview load."""

    def __init__(self, spec):
        self.url = spec["url"]
        self.x, self.y = spec["x"], spec["y"]
        self.w, self.h = spec["w"], spec["h"]
        self.name = spec.get("name", "")
        self.provides_audio = bool(spec.get("audio", False))
        self.lang = spec.get("lang", "und")
        self.featured = bool(spec.get("featured", False))
        self.frame_reduction = FrameReduction(spec.get("output_fps", "30"))
        self.hwaccel = spec.get("hwaccel")
        self.hwaccel_device = spec.get("hwaccel_device")
        self.valign = spec.get("valign", "center")
        self.halign = spec.get("halign", "center")
        self.tile_stale_secs = max(TILE_STALE_SECS, float(spec.get("tile_hold_secs", TILE_STALE_SECS)))
        self.fallback = (*black_planes(self.w, self.h), 0, 0, self.w, self.h)
        self.latest = self.fallback
        self.fresh_until = 0.0
        logo = spec.get("logo")
        if logo:
            threading.Thread(target=self._load_logo, args=(logo,), daemon=True).start()
        self.running = True
        self.vcount = 0          # decoded video frames (for rate diagnostics)
        self.scaled_vcount = 0
        self.reduced_vcount = 0
        # The compositor selects frames by source PTS. Keeping a short queue
        # decouples that deterministic choice from decoder-thread wake timing.
        self.vlock = threading.Lock()
        self.vframes = collections.deque(maxlen=VIDEO_QUEUE_FRAMES)
        self.display = self.fallback
        # Video presentation has its own anchor. It must be reset after a
        # source rebase without disturbing the decoder/audio PTS clock.
        self.present_pts: "float | None" = None
        self.present_wall: "float | None" = None
        # audio buffer (only used when provides_audio)
        self.alock = threading.Lock()
        self.aframes = []        # list of (pts_s: float|None, ndarray(n,2) int16)
        self.abuffered = 0
        # Decoder PTS clock anchors source pacing and discontinuity detection.
        self.clk_pts: "float | None" = None
        self.clk_wall: "float | None" = None
        # PTS of the audio content most recently handed to the caller by take() —
        # used by audio_feeder() to detect drift against the video clock and
        # periodically re-sync via _align_to_pts(), without waiting for a full
        # clk_pts reset. Protected by self.alock (same as aframes/abuffered).
        self.last_taken_pts: "float | None" = None
        self.audio_resyncs = 0
        self._reconnect_requested = False
        # A provider may change coded size, pixel format, cadence, or PTS epoch
        # without closing this HTTP child stream. Preserve the current tile
        # through that handoff and resume only from a decoded key frame.
        self.awaiting_keyframe = True
        self.recovery_reason = "startup"
        self.recovery_discarded_frames = 0
        self.recovery_count = 0
        self.input_format = None
        self.audio_fade_started = None

    def _open_container(self):
        if self.hwaccel:
            try:
                from av.codec.hwaccel import HWAccel, hwdevices_available
                if self.hwaccel not in hwdevices_available():
                    raise RuntimeError(f"PyAV has no {self.hwaccel} device support")
                accel = HWAccel(self.hwaccel, device=self.hwaccel_device,
                                allow_software_fallback=False)
                cont = av.open(self.url, options=DECODE_OPTS, hwaccel=accel)
                log(f"channel {self.name}: hardware decode={self.hwaccel}")
                return cont
            except Exception as e:  # noqa: BLE001
                log(f"channel {self.name}: hardware decode unavailable ({e}); using software")
                self.hwaccel = None
        return av.open(self.url, options=DECODE_OPTS)

    def _make_fallback(self, logo):
        Y, U, V = black_planes(self.w, self.h)
        if logo:
            try:
                with av.open(logo) as c:
                    for frame in c.decode(video=0):
                        # Scale to fit within one-third of the tile, preserving aspect ratio.
                        max_w = (self.w // 3) & ~1
                        max_h = (self.h // 3) & ~1
                        scale = min(max_w / frame.width, max_h / frame.height)
                        lw = _even(frame.width * scale)
                        lh = _even(frame.height * scale)
                        # Decode as RGBA so transparent areas composite cleanly over black.
                        # Use to_ndarray() -- planes[0] has stride padding that makes
                        # raw frombuffer shapes wrong for non-aligned widths.
                        rf = frame.reformat(width=lw, height=lh, format="rgba")
                        arr = rf.to_ndarray(format="rgba")   # (lh, lw, 4), stride-free
                        alpha = arr[:, :, 3:4].astype(np.float32) / 255.0
                        rgb = (arr[:, :, :3] * alpha).astype(np.uint8)
                        rgb_frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
                        lf = rgb_frame.reformat(format="yuv420p")
                        ly, lu, lv = yuv_planes_from_frame(lf, lw, lh)
                        oy = ((self.h - lh) // 2) & ~1
                        ox = ((self.w - lw) // 2) & ~1
                        Y[oy:oy + lh, ox:ox + lw] = ly
                        U[oy // 2:(oy + lh) // 2, ox // 2:(ox + lw) // 2] = lu
                        V[oy // 2:(oy + lh) // 2, ox // 2:(ox + lw) // 2] = lv
                        break
            except Exception as e:  # noqa: BLE001
                log(f"logo decode failed for {self.name}: {e}")
        return (Y, U, V, 0, 0, self.w, self.h)

    def _load_logo(self, logo):
        """Load logo in background and swap self.fallback when ready."""
        fb = self._make_fallback(logo)
        self.fallback = fb                  # CPython GIL makes tuple attr swap atomic
        if self.fresh_until == 0.0:         # no real video yet; update latest too
            self.latest = fb

    def reconnect(self):
        """Signal run() to drop and reconnect; checked inside the demux loop."""
        self._reconnect_requested = True

    def _begin_recovery(self, reason, retain_frame=True):
        """Reset timing state while preserving a valid tile through warm-up."""
        self.awaiting_keyframe = True
        self.recovery_reason = reason
        self.recovery_discarded_frames = 0
        self.frame_reduction.reset()
        self.clk_pts = None
        self.clk_wall = None
        with self.alock:
            self.aframes.clear()
            self.abuffered = 0
            self.last_taken_pts = None
        with self.vlock:
            self.vframes.clear()
            self.present_pts = None
            self.present_wall = None
            if not retain_frame:
                self.display = self.fallback
                self.latest = self.fallback
                self.fresh_until = 0.0
        self.audio_fade_started = None
        log(f"channel {self.name}: recovering ({reason}); waiting for keyframe")

    def _resume_on_keyframe(self, pts_s, now):
        self.awaiting_keyframe = False
        self.recovery_count += 1
        self.clk_pts, self.clk_wall = (pts_s, now) if pts_s is not None else (None, None)
        self.audio_fade_started = now
        log(f"channel {self.name}: resumed on keyframe after {self.recovery_discarded_frames} "
            f"frames ({self.recovery_reason})")

    def audio_gain(self, now):
        """Fade PCM in after recovered video replaces the held frame."""
        started = self.audio_fade_started
        if started is None:
            return 1.0
        return min(1.0, max(0.0, (now - started) / AUDIO_RECOVERY_FADE_SECS))

    def recovery_status(self):
        fps = self.frame_reduction.source_fps()
        fmt = "?" if self.input_format is None else "x".join(map(str, self.input_format[:2]))
        cadence = "?" if fps is None else f"{fps:.2f}"
        return f"fmt{fmt}/src{cadence}/wait{int(self.awaiting_keyframe)}/r{self.recovery_count}"

    def run(self):
        failures = 0
        while self.running:
            if self._reconnect_requested:
                self._reconnect_requested = False
                failures = 0
            if failures >= RECONNECT_RETRIES:
                log(f"channel {self.name}: giving up after {RECONNECT_RETRIES} failed retries")
                break
            cont = None
            # Keep an already displayed image while a replacement connection
            # warms up. Initial startup has no frame to preserve.
            self._begin_recovery("connection opened", retain_frame=self.vcount > 0)
            last_video_pts = None
            last_video_pts_progress = None
            video_pts_stalled = False
            vcount_before = self.vcount
            try:
                cont = self._open_container()
                vs = cont.streams.video[0]
                # Multi-threaded decode so 1080p sources keep up with the output
                # rate (single-threaded PyAV decode runs ~22-27fps -> slow motion).
                vs.thread_type = "AUTO"
                vs.codec_context.thread_count = 3
                log(f"channel {self.name}: video codec={getattr(vs.codec_context, 'name', None)} "
                    f"size={vs.width}x{vs.height} avg_rate={getattr(vs, 'average_rate', None)} "
                    f"base_rate={getattr(vs, 'base_rate', None)} "
                    f"field_order={getattr(vs.codec_context, 'field_order', None)}")
                # Lower-effort decode for non-featured tiles: skip the deblocking
                # loop filter. Big decode-CPU saving; the minor blockiness is
                # hidden by downscaling small tiles. The featured tile keeps full
                # deblocking so it stays sharp.
                if not self.featured:
                    try:
                        vs.codec_context.skip_loop_filter = "ALL"
                    except Exception:
                        pass
                streams = [vs]
                res = None
                aus = None
                if self.provides_audio and cont.streams.audio:
                    aus = cont.streams.audio[0]
                    streams.append(aus)
                    res = av.AudioResampler(format="s16", layout=AUDIO_LAYOUT, rate=AUDIO_RATE)
                try:
                    for packet in cont.demux(*streams):
                        if not self.running or self._reconnect_requested:
                            break
                        if packet.dts is None:
                            continue
                        if packet.stream.type == "video":
                            for frame in decode_packet(
                                    packet,
                                    lambda e: log(f"channel {self.name}: dropping bad video packet: {e}")):
                                self.vcount += 1
                                frame_format = (frame.width, frame.height,
                                                getattr(frame.format, "name", None))
                                if source_format_changed(self.input_format, frame_format):
                                    self._begin_recovery(
                                        f"format {self.input_format} -> {frame_format}")
                                self.input_format = frame_format
                                pts_s = None
                                now = time.monotonic()
                                if frame.pts is not None:
                                    pts_s = float(frame.pts * vs.time_base)
                                    if is_pts_progress(last_video_pts, pts_s):
                                        if (last_video_pts is not None
                                                and pts_s <= last_video_pts - PTS_DISCONTINUITY_SECS):
                                            log(f"channel {self.name}: source PTS reset "
                                                f"({pts_s - last_video_pts:+.1f}s)")
                                        last_video_pts = pts_s
                                        last_video_pts_progress = now
                                        if video_pts_stalled:
                                            log(f"channel {self.name}: video PTS resumed")
                                            video_pts_stalled = False
                                    elif (last_video_pts_progress is not None
                                          and now - last_video_pts_progress >= VIDEO_PTS_STALL_SECS):
                                        if not video_pts_stalled:
                                            log(f"channel {self.name}: video PTS stalled for "
                                                f"{now - last_video_pts_progress:.1f}s; using fallback")
                                            video_pts_stalled = True
                                            self.clk_pts = None
                                            self.clk_wall = None
                                            with self.vlock:
                                                self.vframes.clear()
                                                self.display = self.fallback
                                                self.fresh_until = 0.0
                                                self.present_pts = None
                                                self.present_wall = None
                                        continue
                                    if video_pts_stalled:
                                        continue
                                    if self.clk_pts is not None:
                                        gap = (self.clk_wall + pts_s - self.clk_pts) - now
                                        if 0 < gap < 2.0:
                                            time.sleep(gap)
                                        elif is_pts_discontinuity(gap):
                                            self._begin_recovery(f"PTS discontinuity {gap:+.1f}s")
                                if not frame_can_resume(self.awaiting_keyframe,
                                                        bool(getattr(frame, "key_frame", False)
                                                             or getattr(packet, "is_keyframe", False))):
                                    self.recovery_discarded_frames += 1
                                    continue
                                if self.awaiting_keyframe:
                                    self._resume_on_keyframe(pts_s, now)
                                if not self.frame_reduction.keep(pts_s):
                                    self.reduced_vcount += 1
                                    continue
                                tile = fit_into_tile(frame, self.w, self.h, self.valign, self.halign)
                                with self.vlock:
                                    self.latest = tile
                                    self.fresh_until = time.monotonic() + self.tile_stale_secs
                                    if frame.pts is not None:
                                        self.vframes.append((pts_s, tile))
                                    else:
                                        self.display = tile
                                self.scaled_vcount += 1
                        elif res is not None and packet.stream.type == "audio":
                            for frame in decode_packet(
                                    packet,
                                    lambda e: log(f"channel {self.name}: dropping bad audio packet: {e}")):
                                pts_s = (float(frame.pts * aus.time_base)
                                         if frame.pts is not None else None)
                                resampled, res = resample_frame(
                                    res, frame,
                                    lambda: av.AudioResampler(
                                        format="s16", layout=AUDIO_LAYOUT, rate=AUDIO_RATE),
                                    lambda action, e: log(
                                        f"channel {self.name}: "
                                        f"{'resetting resampler after audio format change' if action == 'reset' else 'dropping incompatible audio frame'}: {e}"
                                    ),
                                )
                                for rf in resampled:
                                    a = rf.to_ndarray()
                                    a = a.reshape(-1, 2) if a.shape[0] == 1 else a.T
                                    with self.alock:
                                        self.aframes.append((pts_s, a.astype(np.int16)))
                                        self.abuffered += a.shape[0]
                                        self._trim()
                finally:
                    if res is not None:
                        try:
                            res.close()
                        except Exception:
                            pass
            except Exception as e:  # noqa: BLE001
                log(f"channel {self.name} ended: {e}")
            finally:
                if cont is not None:
                    try:
                        cont.close()
                    except Exception:
                        pass
            if self.vcount > vcount_before:
                failures = 0
            else:
                failures += 1
            if self.running and failures < RECONNECT_RETRIES:
                delay = min(RECONNECT_BASE * (2 ** (failures - 1)), RECONNECT_MAX)
                log(f"channel {self.name}: retry {failures}/{RECONNECT_RETRIES} in {delay:.0f}s")
                time.sleep(delay)

    def current_at(self, wall_time):
        """Return the newest decoded frame due at *wall_time* by source PTS."""
        with self.vlock:
            if held_frame(self.latest, self.fallback, self.fresh_until, time.monotonic()) is self.fallback:
                return self.fallback
            if not self.vframes:
                return self.display
            self.present_pts, self.present_wall, target_pts = presentation_target(
                self.present_pts, self.present_wall, self.vframes[0][0], wall_time,
            )
            while self.vframes and self.vframes[0][0] <= target_pts:
                _, self.display = self.vframes.popleft()
            return self.display

    def video_queue_depth(self):
        with self.vlock:
            return len(self.vframes)

    def presentation_pts_now(self, wall_time):
        """Source PTS of the video being presented at ``wall_time``."""
        with self.vlock:
            return anchored_pts(self.present_pts, self.present_wall, wall_time)

    def _trim(self):
        cap = AUDIO_RATE * 2  # ~2s
        while self.abuffered > cap and self.aframes:
            _, drop = self.aframes.pop(0)
            self.abuffered -= drop.shape[0]

    def _align_to_pts(self, pts_limit: float):
        """Discard audio samples before pts_limit."""
        with self.alock:
            # The prior emitted PTS no longer represents the FIFO after this
            # discard. Let the next take() establish a fresh drift baseline.
            self.last_taken_pts = None
            while self.aframes:
                pts_s, chunk = self.aframes[0]
                if pts_s is None:
                    break
                stale = samples_before_pts(pts_s, chunk.shape[0], pts_limit, AUDIO_RATE)
                if stale == chunk.shape[0]:
                    self.aframes.pop(0)
                    self.abuffered -= chunk.shape[0]
                elif stale:
                    self.aframes[0] = (pts_s + stale / AUDIO_RATE, chunk[stale:])
                    self.abuffered -= stale
                    break
                else:
                    break

    def audio_status(self):
        """Return a consistent snapshot for compositor A/V diagnostics."""
        with self.alock:
            first_pts = self.aframes[0][0] if self.aframes else None
            return self.last_taken_pts, self.abuffered, self.audio_resyncs, first_pts

    def take(self, nsamples: int, pts_limit=None) -> np.ndarray:
        """Return PCM through *pts_limit*, silence-padding future samples."""
        out = np.zeros((nsamples, 2), np.int16)
        filled = 0
        with self.alock:
            while filled < nsamples and self.aframes:
                pts_s, chunk = self.aframes[0]
                available = samples_before_pts(pts_s, chunk.shape[0], pts_limit, AUDIO_RATE)
                if available == 0:
                    break
                need = min(nsamples - filled, available)
                if chunk.shape[0] <= need:
                    out[filled:filled + chunk.shape[0]] = chunk
                    self.aframes.pop(0)
                    self.abuffered -= chunk.shape[0]
                    filled += chunk.shape[0]
                    if pts_s is not None:
                        self.last_taken_pts = pts_s + chunk.shape[0] / AUDIO_RATE
                else:
                    out[filled:filled + need] = chunk[:need]
                    # A buffered chunk's PTS always identifies its first retained
                    # sample. Without this adjustment, each partial read makes
                    # drift detection measure from the chunk's original start.
                    next_pts = pts_s + need / AUDIO_RATE if pts_s is not None else None
                    self.aframes[0] = (next_pts, chunk[need:])
                    self.abuffered -= need
                    filled += need
                    if pts_s is not None:
                        self.last_taken_pts = pts_s + need / AUDIO_RATE
                if available < chunk.shape[0]:
                    break
        return out
