"""FFmpeg command builders for the multiview pipeline.

Three kinds of process:

  * placeholder encoder  - lavfi logo/offline card -> normalized tile TS
  * live tile encoder    - real channel (HTTP) -> normalized tile TS
  * composition encoder  - N normalized tile TS -> final multiview output

The placeholder and live tile encoders MUST emit byte-identical stream
parameters (same codec/profile/resolution/fps and same TS PIDs) so the pipeline
can splice between them at a keyframe without the composition's decoder choking.
That is enforced by both going through encoder_args(target="tile") and the same
TILE_* constants and _tile_mpegts_out() PIDs.

The composition reuses the working layout filter builders in layouts.py and the
audio/metadata helpers in server.py; only the encoder selection lives here.
"""

from . import layouts as _layouts

# Normalized tile stream. Fixed so placeholder<->live splices are seamless and
# the composition can scale each tile down to its slot via the layout filter.
TILE_W = 1280
TILE_H = 720
TILE_FPS = "30000/1001"
TILE_VIDEO_BITRATE = 2500  # kbps; tiles are downscaled in the composite anyway
TILE_AUDIO_BITRATE = 128   # kbps

# Fixed TS identifiers shared by placeholder and live tile encoders.
TS_SERVICE_ID = 1
TS_PMT_PID = 0x1000   # 4096
TS_START_PID = 0x100  # 256 -> video 0x100, audio 0x101 (muxer auto-increments)
VIDEO_PID = TS_START_PID
AUDIO_PID = TS_START_PID + 1

_NVENC_PRESETS = {"p1", "p2", "p3", "p4", "p5", "p6", "p7"}
_QSV_PRESETS = {"veryfast", "faster", "fast", "medium", "slow"}
_X264_PRESETS = {"ultrafast", "superfast", "veryfast", "faster", "fast",
                 "medium", "slow", "slower", "veryslow", "placebo"}


def _encoder(settings: dict) -> str:
    return settings.get("video_encoder") or "libx264"


def global_input_args(settings: dict) -> list:
    """Top-level args that must precede inputs (VAAPI device)."""
    if _encoder(settings) == "h264_vaapi":
        dev = settings.get("vaapi_device") or "/dev/dri/renderD128"
        return ["-vaapi_device", dev]
    return []


def video_filter_suffix(settings: dict) -> str:
    """Filter tail appended to a yuv420p [v] label before a VAAPI encoder."""
    if _encoder(settings) == "h264_vaapi":
        return ",hwupload,format=vaapi"
    return ""


def encoder_args(settings: dict, target: str) -> list:
    """Video -c:v ... args for the configured encoder.

    target="tile":  small GOP + frequent keyframes so a placeholder->live splice
                    lands quickly; fixed modest bitrate.
    target="final": uses the user's bitrate/crf with a 2s GOP.
    """
    enc = _encoder(settings)
    if target == "tile":
        bitrate = TILE_VIDEO_BITRATE
        gop, keyint = 15, 15          # ~0.5s at 30fps
        crf = 26
        kf_expr = "expr:gte(t,n_forced*0.5)"
    else:
        bitrate = int(settings.get("output_bitrate") or 8000)
        gop, keyint = 60, 60          # ~2s
        crf = int(settings.get("output_crf") or 23)
        kf_expr = "expr:gte(t,n_forced*2)"
    preset = settings.get("encoder_preset") or "ultrafast"

    if enc == "h264_nvenc":
        p = preset if preset in _NVENC_PRESETS else "p1"
        return ["-c:v", "h264_nvenc", "-preset", p, "-tune", "ll",
                "-rc", "vbr", "-cq", str(crf),
                "-maxrate", f"{bitrate}k", "-bufsize", f"{bitrate * 2}k",
                "-g", str(gop), "-keyint_min", str(keyint)]
    if enc == "h264_qsv":
        p = preset if preset in _QSV_PRESETS else "veryfast"
        return ["-c:v", "h264_qsv", "-preset", p, "-global_quality", str(crf),
                "-b:v", f"{bitrate}k", "-maxrate", f"{bitrate}k",
                "-bufsize", f"{bitrate * 2}k", "-g", str(gop), "-low_power", "1"]
    if enc == "h264_vaapi":
        return ["-c:v", "h264_vaapi",
                "-b:v", f"{bitrate}k", "-maxrate", f"{bitrate}k",
                "-bufsize", f"{bitrate * 2}k", "-g", str(gop)]
    # libx264
    p = preset if preset in _X264_PRESETS else "ultrafast"
    return ["-c:v", "libx264", "-preset", p, "-tune", "zerolatency",
            "-profile:v", "main", "-level:v", "4.1", "-pix_fmt", "yuv420p",
            "-crf", str(crf), "-maxrate", f"{bitrate}k", "-bufsize", f"{bitrate * 2}k",
            "-g", str(gop), "-keyint_min", str(keyint),
            "-sc_threshold", "0", "-force_key_frames", kf_expr]


def _tile_audio_args() -> list:
    return ["-c:a", "ac3", "-b:a", f"{TILE_AUDIO_BITRATE}k", "-ar", "48000", "-ac", "2"]


def _tile_mpegts_out() -> list:
    """mpegts muxer args giving every tile identical, fixed PIDs."""
    return [
        "-mpegts_service_id", str(TS_SERVICE_ID),
        "-mpegts_pmt_start_pid", str(TS_PMT_PID),
        "-mpegts_start_pid", str(TS_START_PID),
        "-mpegts_flags", "+resend_headers+pat_pmt_at_frames",
        "-muxpreload", "0", "-muxdelay", "0",
        "-f", "mpegts", "pipe:1",
    ]


def build_placeholder_cmd(settings: dict, logo_path: "str | None", bg: str = "black") -> list:
    """Logo placeholder / offline card -> normalized tile TS on stdout.

    Same encoder/PIDs as the live tile so the splice is seamless. `bg` is the
    lavfi background color: "black" for the startup logo, a distinct color (e.g.
    a dark red) for the offline card. We use the background color rather than
    drawtext to distinguish states because the `color` filter is always present,
    whereas `drawtext` needs libfreetype which some ffmpeg builds lack.
    """
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
    cmd += global_input_args(settings)
    cmd += ["-f", "lavfi", "-i", f"color=c={bg}:s={TILE_W}x{TILE_H}:r={TILE_FPS}"]
    cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]

    suffix = video_filter_suffix(settings)

    if logo_path:
        side = min(TILE_W, TILE_H) // 3
        fc = (f"[2:v]scale={side}:{side}:force_original_aspect_ratio=decrease,setsar=1[logo];"
              f"[0:v][logo]overlay=(W-w)/2:(H-h)/2{suffix}[v]")
        cmd += ["-loop", "1", "-framerate", TILE_FPS, "-i", logo_path]
        cmd += ["-filter_complex", fc, "-map", "[v]"]
    else:
        fc = f"[0:v]null{suffix}[v]"
        cmd += ["-filter_complex", fc, "-map", "[v]"]

    cmd += ["-map", "1:a"]
    cmd += encoder_args(settings, "tile")
    cmd += _tile_audio_args()
    cmd += _tile_mpegts_out()
    return cmd


def build_live_tile_cmd(settings: dict, realsrc_url: str) -> list:
    """Real channel (HTTP TS from the realsrc endpoint) -> normalized tile TS.

    Scales/pads to the fixed tile size and re-encodes to identical params so it
    can be spliced in behind the placeholder. First audio track is required; a
    channel with no audio will fail to encode and the pipeline keeps the
    placeholder (acceptable for v1).
    """
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
    cmd += global_input_args(settings)
    cmd += [
        "-fflags", "+discardcorrupt+genpts",
        "-analyzeduration", "2000000", "-probesize", "1000000",
        "-thread_queue_size", "1024",
        "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
        "-f", "mpegts", "-i", realsrc_url,
    ]
    suffix = video_filter_suffix(settings)
    fc = (f"[0:v]fps={TILE_FPS},"
          f"scale={TILE_W}:{TILE_H}:force_original_aspect_ratio=decrease,"
          f"pad={TILE_W}:{TILE_H}:(ow-iw)/2:(oh-ih)/2,setsar=1{suffix}[v]")
    cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "0:a:0"]
    cmd += encoder_args(settings, "tile")
    cmd += _tile_audio_args()
    cmd += _tile_mpegts_out()
    return cmd


def build_composition_cmd(tile_urls: list, layout: str, settings: dict,
                          audio_source: str, channel_names: list,
                          audio_metadata_args: list) -> list:
    """N normalized tile TS inputs -> final multiview output on stdout.

    Tiles are already normalized, so the layout filter just scales each to its
    slot and stacks. Audio is selected from the tile inputs the same way the old
    server did. `audio_metadata_args` is server._audio_metadata_args(...).
    """
    n = len(tile_urls)
    out_w, out_h = _parse_resolution(settings)

    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
    cmd += global_input_args(settings)
    for url in tile_urls:
        cmd += [
            "-fflags", "+genpts",
            "-use_wallclock_as_timestamps", "1",
            "-thread_queue_size", "1024",
            "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "2",
            "-f", "mpegts", "-i", url,
        ]

    if layout == "featured":
        filter_complex, map_args = _layouts._featured_filter(n, out_w, out_h)
    elif layout == "top_featured":
        filter_complex, map_args = _layouts._top_featured_filter(n, out_w, out_h)
    else:
        filter_complex, map_args = _layouts._auto_grid_filter(n, out_w, out_h)

    if _encoder(settings) == "h264_vaapi":
        filter_complex = filter_complex.replace("[v]", "[vraw]", 1)
        filter_complex += "; [vraw]hwupload,format=vaapi[v]"

    cmd += ["-filter_complex", filter_complex]
    cmd += map_args

    cmd += encoder_args(settings, "final")

    if audio_source == "all":
        for i in range(n):
            cmd += ["-map", f"{i}:a?"]
        cmd += ["-c:a", "ac3", "-af", "aresample=async=1000"]
    else:
        audio_idx = int(audio_source) if str(audio_source).isdigit() else 0
        audio_idx = max(0, min(audio_idx, n - 1))
        cmd += ["-map", f"{audio_idx}:a", "-c:a", "ac3", "-af", "aresample=async=1000"]
    cmd += audio_metadata_args

    cmd += [
        "-max_muxing_queue_size", "1024",
        "-mpegts_flags", "+pat_pmt_at_frames+resend_headers+initial_discontinuity",
        "-f", "mpegts", "pipe:1",
    ]
    return cmd


def _parse_resolution(settings: dict) -> tuple:
    try:
        w, h = (int(x) for x in (settings.get("output_resolution") or "1920x1080").split("x"))
        return w, h
    except Exception:
        return 1920, 1080
