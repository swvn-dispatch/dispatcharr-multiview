"""Multiview compositor worker (separate process, no gevent).

Spawned by server._serve_stream as `python compositor_worker.py <config-json>`. It
runs as a plain CPython process (so real threads parallelize across cores and
nothing fights Dispatcharr's gevent hub). Config schema (argv[1] JSON):

  {"out_w","out_h","fps","bitrate","crf","preset",
   "tiles":[{"url","x","y","w","h","logo","name"}...],
   "audio":[{"url","name","lang"}...]}

The Channel class (decode, YUV compositing, audio buffering) and its PyAV/numpy
dependencies live in channel.py. This module handles encoder setup and the
main compositing loop.
"""

import json
import os
import subprocess
import sys
import threading
import time
from fractions import Fraction

# channel.py sets up the vendored PyAV sys.path as a side effect of import;
# numpy must be imported after so it finds the vendored build.
from channel import Channel, AUDIO_RATE, AUDIO_LAYOUT, log, _yuv_planes  # noqa: E402

import numpy as np  # noqa: E402


def fps_fraction(fps: str) -> Fraction:
    if "/" in fps:
        a, b = fps.split("/")
        return Fraction(int(a), int(b))
    return Fraction(int(fps), 1)


# ---------------------------------------------------------------- encoder

def _write_all(fd, data):
    mv = memoryview(data)
    while mv:
        try:
            k = os.write(fd, mv)
        except OSError:
            return False
        mv = mv[k:]
    return True


def _nvenc_available() -> bool:
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=5,
        )
        return "h264_nvenc" in r.stdout
    except Exception:
        return False


def build_encoder_cmd(cfg, out_w, out_h, audio_read):
    bitrate = int(cfg.get("bitrate", 8000))
    gop = max(2, round(float(fps_fraction(cfg["fps"])) * 2))
    encoder = cfg.get("video_encoder", "libx264")
    preset = cfg.get("preset") or ("p4" if encoder == "h264_nvenc" else "ultrafast")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error",
           # Cap muxer/filter threads so it doesn't grab every core and starve
           # the PyAV decoders (3x 1080p60 decode already loads the box).
           "-threads", str(cfg.get("enc_threads", 4)),
           "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{out_w}x{out_h}",
           "-r", cfg["fps"], "-thread_queue_size", "512", "-i", "pipe:0"]
    for r in audio_read:
        cmd += ["-f", "s16le", "-ar", str(AUDIO_RATE), "-ac", "2",
                "-thread_queue_size", "512", "-i", f"pipe:{r}"]
    cmd += ["-map", "0:v:0"]
    for i in range(len(audio_read)):
        cmd += ["-map", f"{i + 1}:a:0"]
    # VBV CBR: constant bitrate regardless of content complexity. CRF (VBR)
    # produces near-zero bitrate for static/logo content; IPTV players drain
    # their receive buffer faster than realtime when the rate is very low,
    # causing fast-forward. CBR pads with filler NAL units to hold constant
    # rate. bufsize = 0.5x target keeps encode latency low.
    # -muxrate is NOT used: CBR already guarantees constant output rate;
    # -muxrate adds MPEG-TS null packets that shift the PCR clock away from
    # video PTS, causing player sync issues.
    if encoder == "h264_nvenc":
        # NVENC CBR via -rc cbr (pads with filler NAL units, same guarantee as
        # x264 CBR). -minrate, -keyint_min, -sc_threshold are x264-only.
        cmd += ["-c:v", "h264_nvenc", "-preset", preset,
                "-rc", "cbr",
                "-pix_fmt", "yuv420p",
                "-b:v", f"{bitrate}k",
                "-maxrate", f"{bitrate}k",
                "-bufsize", f"{bitrate // 2}k",
                "-g", str(gop)]
    else:
        cmd += ["-c:v", "libx264", "-preset", preset,
                "-pix_fmt", "yuv420p",
                "-b:v", f"{bitrate}k",
                "-minrate", f"{bitrate}k",
                "-maxrate", f"{bitrate}k",
                "-bufsize", f"{bitrate // 2}k",
                "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0"]
    if audio_read:
        cmd += ["-c:a", "ac3", "-b:a", "192k"]
    cmd += ["-max_muxing_queue_size", "1024",
            "-mpegts_flags", "+pat_pmt_at_frames+resend_headers+initial_discontinuity",
            "-flush_packets", "1", "-f", "mpegts", "pipe:1"]
    return cmd


def audio_feeder(track, fd, stop):
    CHUNK = int(AUDIO_RATE * 0.02)  # 960 samples = 20ms per tick
    SILENCE = np.zeros((CHUNK, 2), dtype=np.int16)

    # Phase 1: wait for the video PTS clock to establish, then snap the audio
    # buffer to that position. This discards any audio Dispatcharr pre-buffered
    # ahead of realtime before we start constant-rate output.
    while not stop.is_set():
        pts_now = track.audio_pts_now()
        if pts_now is not None:
            track._align_to_pts(pts_now - 0.10)
            break
        _write_all(fd, SILENCE.tobytes())
        time.sleep(0.02)

    # Phase 2: constant wall-clock rate. Smooth output is more important than
    # perfect PTS tracking; the reconnect flush in Channel.run() handles the
    # stale-audio problem, so wall-clock pacing is safe here.
    start = time.monotonic()
    written = 0
    while not stop.is_set():
        target = int((time.monotonic() - start) * AUDIO_RATE)
        need = target - written
        if need > 0:
            pcm = track.take(need)
            if not _write_all(fd, pcm.tobytes()):
                break
            written += need
        time.sleep(0.02)


# ---------------------------------------------------------------- main

def main():
    cfg = json.loads(sys.argv[1])
    out_w, out_h = cfg["out_w"], cfg["out_h"]
    fps_f = float(fps_fraction(cfg["fps"]))
    channels = [Channel(t) for t in cfg["tiles"]]
    audio_chs = [c for c in channels if c.provides_audio]
    stop = threading.Event()

    for c in channels:
        threading.Thread(target=c.run, name=f"chan-{c.name}", daemon=True).start()

    # ffmpeg encodes (libx264, multi-core C) + muxes; we feed it the composited
    # yuv420p canvas on stdin and one PCM track per audio channel on inherited fds.
    video_r, video_w = os.pipe()
    audio_pipes = [os.pipe() for _ in audio_chs]
    audio_read = [r for (r, _w) in audio_pipes]
    enc_out_r, enc_out_w = os.pipe()
    if cfg.get("video_encoder") == "h264_nvenc" and not _nvenc_available():
        sys.exit("h264_nvenc selected but ffmpeg reports no NVENC encoder -- "
                 "check NVIDIA driver and ffmpeg build")
    cmd = build_encoder_cmd(cfg, out_w, out_h, audio_read)
    for i, a in enumerate(audio_chs):
        cmd[-1:-1] = [f"-metadata:s:a:{i}", f"title={a.name}",
                      f"-metadata:s:a:{i}", f"language={a.lang}"]
    enc = subprocess.Popen(cmd, stdin=video_r, stdout=enc_out_w,
                           stderr=sys.stderr, pass_fds=audio_read)
    os.close(video_r)
    os.close(enc_out_w)
    for r in audio_read:
        os.close(r)
    audio_w = [w for (_r, w) in audio_pipes]

    # Forward the encoder's mpegts to our stdout (read by the plugin's server).
    def pump_out():
        wout = sys.stdout.buffer
        while True:
            b = os.read(enc_out_r, 65536)
            if not b:
                break
            try:
                wout.write(b)
                wout.flush()
            except (BrokenPipeError, ValueError):
                break
        stop.set()
    threading.Thread(target=pump_out, name="pump-out", daemon=True).start()

    for a, fd in zip(audio_chs, audio_w):
        threading.Thread(target=audio_feeder, args=(a, fd, stop), daemon=True).start()

    # yuv420p canvas as one flat buffer (Y|U|V) with plane views; writing the
    # whole buffer is exactly the planar byte order ffmpeg's rawvideo wants.
    ysize = out_w * out_h
    csize = (out_w // 2) * (out_h // 2)
    cbuf = np.zeros(ysize + 2 * csize, np.uint8)
    Yc, Uc, Vc = _yuv_planes(cbuf, out_w, out_h)
    Uc[:] = 128
    Vc[:] = 128

    start = time.monotonic()
    n = 0
    log_at = start + 30.0
    prev_t = start
    prev_counts = [0] * len(channels)
    log(f"started: {len(channels)} tiles, {len(audio_chs)} audio, {out_w}x{out_h}@{cfg['fps']}")
    try:
        while not stop.is_set():
            for t in channels:
                Yt, Ut, Vt = t.current()
                x, y, w, h = t.x, t.y, t.w, t.h
                Yc[y:y + h, x:x + w] = Yt
                Uc[y // 2:(y + h) // 2, x // 2:(x + w) // 2] = Ut
                Vc[y // 2:(y + h) // 2, x // 2:(x + w) // 2] = Vt
            if not _write_all(video_w, memoryview(cbuf)):
                break
            n += 1
            now = time.monotonic()
            if now >= log_at:   # heartbeat: per-channel decode fps (CPU health)
                dt = now - prev_t
                rates = " ".join(f"{c.name[:7]}={(c.vcount - prev_counts[i]) / dt:.0f}fps"
                                 for i, c in enumerate(channels))
                import resource as _res
                rss_mb = _res.getrusage(_res.RUSAGE_SELF).ru_maxrss // 1024
                log(f"out {n / (now - start):.1f}fps; decode {rates}; rss={rss_mb}MB")
                prev_counts = [c.vcount for c in channels]
                prev_t = now
                log_at = now + 30.0
            delay = (start + n / fps_f) - now
            if delay > 0:
                time.sleep(delay)
    except (BrokenPipeError, KeyboardInterrupt):
        pass
    finally:
        stop.set()
        for c in channels:
            c.running = False
        for fd in audio_w:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.close(video_w)
        except OSError:
            pass
        try:
            enc.wait(timeout=3)
        except Exception:
            enc.kill()
        try:
            os.close(enc_out_r)
        except OSError:
            pass


if __name__ == "__main__":
    main()
