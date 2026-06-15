"""Multiview streaming server — Phase 1.

Phase 1: main composite stream with a lavfi placeholder per channel slot.
         The composition ffmpeg starts and streams immediately; channels show
         a "Loading..." tile until Phase 2 replaces them with real streams.

Phase 2 (future): per-channel endpoint switches from lavfi placeholder to a
                  real Dispatcharr channel stream.

Routes:
  GET /health              Health check
  GET /stream/{n}          MPEG-TS multiview stream for layout n (1-based)
  GET /internal/ch/{uuid}  Single-channel lavfi placeholder (Phase 1)
"""

import logging
import os
import re
import socket
import subprocess
import threading
import uuid as _uuid_module

from . import layouts as _layouts

logger = logging.getLogger(__name__)

CHUNK_SIZE = 65536

_server_instance = None


def get_server():
    return _server_instance


def set_server(s):
    global _server_instance
    _server_instance = s


def _kill_proc(proc) -> None:
    try:
        proc.kill()
        proc.wait()
    except Exception:
        pass


def _log_stderr(proc, label: str) -> None:
    try:
        for raw in proc.stderr:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                logger.warning(f"ffmpeg {label}: {line}")
    except Exception:
        pass


def _lang_code(name: str) -> str:
    name = re.sub(r'^[A-Z0-9]{2,5}\s*[|–—-]\s*', '', name)
    clean = "".join(c for c in name if c.isalnum() or c == " ").strip()
    words = clean.split()
    if len(words) <= 1:
        return ((words[0] if words else "unk") + "   ")[:3].lower()
    sig = [w for w in words if len(w) >= 2 and w.isupper()]
    if sig:
        return ("".join(sig) + "   ")[:3].lower()
    initials = "".join(w[0] for w in words if w)
    return (initials + "   ")[:3].lower()


def _deduplicate_lang_codes(names: list[str]) -> list[str]:
    raw = [_lang_code(n) for n in names]
    counts: dict[str, int] = {}
    for c in raw:
        counts[c] = counts.get(c, 0) + 1
    seen: dict[str, int] = {}
    result = []
    for code in raw:
        if counts[code] > 1:
            seen[code] = seen.get(code, 0) + 1
            result.append(code[:2] + str(seen[code]))
        else:
            result.append(code)
    return result


def _parse_resolution(settings: dict) -> tuple[int, int]:
    try:
        w, h = (int(x) for x in (settings.get("output_resolution") or "1920x1080").split("x"))
        return w, h
    except Exception:
        return 1920, 1080


def _usable_logo(url) -> "str | None":
    """Return url only if it's a local file path that exists on disk."""
    if url and isinstance(url, str) and url.startswith("/"):
        try:
            if os.path.isfile(url):
                return url
        except Exception:
            pass
    return None


def _audio_metadata_args(audio_source: str, channel_names: list[str], n: int) -> list:
    args = []
    if audio_source == "all":
        lang_codes = _deduplicate_lang_codes(channel_names or [])
        for i, (name, code) in enumerate(zip(channel_names or [], lang_codes)):
            args += [f"-metadata:s:a:{i}", f"title={name}",
                     f"-metadata:s:a:{i}", f"language={code}"]
    else:
        audio_idx = int(audio_source) if str(audio_source).isdigit() else 0
        audio_idx = max(0, min(audio_idx, n - 1))
        if channel_names and audio_idx < len(channel_names):
            name = channel_names[audio_idx]
            args += ["-metadata:s:a:0", f"title={name}",
                     "-metadata:s:a:0", f"language={_lang_code(name)}"]
    return args


class MultiviewServer:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._server = None
        self._greenlet = None
        self.running = False

    def wsgi_app(self, environ, start_response):
        path = environ.get("PATH_INFO", "")

        if path == "/health":
            start_response("200 OK", [("Content-Type", "text/plain")])
            return [b"OK\n"]

        if path.startswith("/stream/"):
            try:
                n = int(path.split("/")[2])
            except (IndexError, ValueError):
                start_response("400 Bad Request", [("Content-Type", "text/plain")])
                return [b"Invalid stream index\n"]
            return self._serve_stream(n, start_response)

        if path.startswith("/internal/ch/"):
            channel_id = path[len("/internal/ch/"):]
            return self._serve_channel(channel_id, start_response)

        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"Not Found\n"]

    def _serve_stream(self, n: int, start_response):
        logger.info(f"Stream request: layout {n}")

        try:
            input_urls, layout, channel_names, logo_urls, audio_source = self._resolve_layout(n)
        except LookupError as e:
            start_response("404 Not Found", [("Content-Type", "text/plain")])
            return [str(e).encode()]
        except Exception as e:
            logger.error(f"Layout {n} error: {e}", exc_info=True)
            start_response("500 Internal Server Error", [("Content-Type", "text/plain")])
            return [b"error\n"]

        try:
            from apps.plugins.models import PluginConfig
            settings = PluginConfig.objects.get(key="multiview").settings
        except Exception:
            settings = {}

        cmd = self._build_composition_cmd(input_urls, layout, settings, audio_source, channel_names)
        logger.info(f"Starting composition ffmpeg: {len(input_urls)} inputs, layout={layout}")

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        threading.Thread(target=_log_stderr, args=(proc, f"composition-{n}"), daemon=True).start()

        start_response("200 OK", [
            ("Content-Type", "video/mp2t"),
            ("Cache-Control", "no-cache"),
            ("X-Accel-Buffering", "no"),
        ])

        def stream_gen():
            try:
                while True:
                    chunk = proc.stdout.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    yield chunk
            except GeneratorExit:
                pass
            finally:
                _kill_proc(proc)
                logger.info(f"Composition stream {n} ended, ffmpeg killed")

        return stream_gen()

    def _serve_channel(self, channel_id: str, start_response):
        try:
            _uuid_module.UUID(channel_id)
        except ValueError:
            start_response("400 Bad Request", [("Content-Type", "text/plain")])
            return [b"Invalid channel ID\n"]

        logo_path = None
        try:
            from apps.channels.models import Channel
            ch = Channel.objects.select_related("logo").get(uuid=channel_id)
            if ch.logo_id is not None:
                logo_path = _usable_logo(ch.logo.url)
        except Exception:
            pass

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning",
            "-f", "lavfi", "-i", "color=c=black:size=640x360:rate=30000/1001",
            "-f", "lavfi", "-i", "aevalsrc=0:channel_layout=stereo:sample_rate=48000",
        ]

        if logo_path:
            cmd += ["-loop", "1", "-framerate", "30000/1001", "-i", logo_path]
            logo_size = 120
            cmd += [
                "-filter_complex",
                f"[2:v]scale={logo_size}:{logo_size}:force_original_aspect_ratio=decrease,setsar=1[logo];"
                "[0:v][logo]overlay=x=(W-w)/2:y=(H-h)/2[v]",
                "-map", "[v]",
            ]
        else:
            cmd += [
                "-vf", "drawtext=text=Loading...:fontcolor=white:fontsize=28:x=(w-tw)/2:y=(h-th)/2",
                "-map", "0:v",
            ]

        cmd += [
            "-map", "1:a",
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
            "-c:a", "ac3", "-b:a", "192k",
            "-f", "mpegts", "pipe:1",
        ]

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        start_response("200 OK", [
            ("Content-Type", "video/mp2t"),
            ("Cache-Control", "no-cache"),
            ("X-Accel-Buffering", "no"),
        ])

        def stream_gen():
            try:
                while True:
                    chunk = proc.stdout.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    yield chunk
            except GeneratorExit:
                pass
            finally:
                _kill_proc(proc)

        return stream_gen()

    def _resolve_layout(self, n: int):
        from apps.plugins.models import PluginConfig
        from apps.channels.models import Channel

        try:
            cfg = PluginConfig.objects.get(key="multiview")
            settings = cfg.settings
        except Exception:
            settings = {}

        ch_count = max(2, int(settings.get(f"multiview_{n}_channel_count", 4)))
        layout = settings.get(f"multiview_{n}_layout", "auto")
        selector_type = settings.get(f"multiview_{n}_selector_type", "classic")

        input_urls: list[str] = []
        channel_names: list[str] = []
        logo_urls: list = []

        if selector_type == "regex":
            pattern = settings.get(f"multiview_{n}_regex_pattern", "").strip()
            if not pattern:
                raise LookupError(f"Layout {n} is in regex mode but has no pattern configured")
            matched = list(
                Channel.objects.select_related("logo")
                .filter(name__iregex=pattern)
                .order_by("channel_number")[:ch_count]
            )
            for ch in matched:
                input_urls.append(f"http://127.0.0.1:{self.port}/internal/ch/{ch.uuid}")
                channel_names.append(ch.name)
                try:
                    logo_urls.append(ch.logo.url if ch.logo_id is not None else None)
                except Exception:
                    logo_urls.append(None)
            audio_source = settings.get(f"multiview_{n}_audio_source", "0")
            if audio_source in ("regex_first", "regex_lowest"):
                audio_source = "0"
        else:
            for m in range(1, ch_count + 1):
                ch_id_str = settings.get(f"multiview_{n}_channel_{m}", "_none")
                if not ch_id_str or ch_id_str == "_none":
                    continue
                try:
                    ch = Channel.objects.select_related("logo").get(id=int(ch_id_str))
                except Channel.DoesNotExist:
                    logger.warning(f"Layout {n} channel slot {m}: id={ch_id_str} not found, skipping")
                    continue
                input_urls.append(f"http://127.0.0.1:{self.port}/internal/ch/{ch.uuid}")
                channel_names.append(ch.name)
                try:
                    logo_urls.append(ch.logo.url if ch.logo_id is not None else None)
                except Exception:
                    logo_urls.append(None)
            audio_source = settings.get(f"multiview_{n}_audio_source", "0")

        if len(input_urls) < 2:
            raise LookupError(
                f"Layout {n} needs at least 2 configured channels (found {len(input_urls)})"
            )

        logger.info(f"Layout {n}: {len(input_urls)} channels, layout={layout}, audio={audio_source}")
        return input_urls, layout, channel_names, logo_urls, audio_source

    def _build_composition_cmd(
        self,
        input_urls: list[str],
        layout: str,
        settings: dict,
        audio_source: str,
        channel_names: list[str],
    ) -> list[str]:
        n = len(input_urls)
        out_w, out_h = _parse_resolution(settings)
        bitrate = int(settings.get("output_bitrate") or 8000)
        crf = int(settings.get("output_crf") or 23)
        preset = settings.get("encoder_preset") or "ultrafast"
        encoder = settings.get("video_encoder") or "libx264"
        vaapi_device = settings.get("vaapi_device") or "/dev/dri/renderD128"

        if encoder == "h264_vaapi":
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning",
                   "-vaapi_device", vaapi_device]
        else:
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]

        for url in input_urls:
            cmd += [
                "-f", "mpegts",
                "-use_wallclock_as_timestamps", "1",
                "-fflags", "+discardcorrupt+genpts+nobuffer",
                "-analyzeduration", "100000",
                "-probesize", "32768",
                "-thread_queue_size", "1024",
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_delay_max", "5",
                "-i", url,
            ]

        if layout == "featured":
            filter_complex, map_args = _layouts._featured_filter(n, out_w, out_h)
        elif layout == "top_featured":
            filter_complex, map_args = _layouts._top_featured_filter(n, out_w, out_h)
        else:
            filter_complex, map_args = _layouts._auto_grid_filter(n, out_w, out_h)

        if encoder == "h264_vaapi":
            filter_complex = filter_complex.replace("[v]", "[vraw]", 1)
            filter_complex += "; [vraw]hwupload,format=vaapi[v]"

        cmd += ["-filter_complex", filter_complex]
        cmd += map_args

        _NVENC_VALID = {"p1", "p2", "p3", "p4", "p5", "p6", "p7"}
        _QSV_VALID = {"veryfast", "faster", "fast", "medium", "slow"}
        _X264_VALID = {"ultrafast", "superfast", "veryfast", "faster", "fast",
                       "medium", "slow", "slower", "veryslow", "placebo"}

        if encoder == "h264_nvenc":
            p = preset if preset in _NVENC_VALID else "p1"
            cmd += ["-c:v", "h264_nvenc", "-preset", p, "-tune", "ll",
                    "-rc", "vbr", "-cq", str(crf),
                    "-maxrate", f"{bitrate}k", "-bufsize", f"{bitrate * 2}k",
                    "-g", "60", "-keyint_min", "60"]
        elif encoder == "h264_qsv":
            p = preset if preset in _QSV_VALID else "veryfast"
            cmd += ["-c:v", "h264_qsv", "-preset", p, "-global_quality", str(crf),
                    "-b:v", f"{bitrate}k", "-maxrate", f"{bitrate}k",
                    "-bufsize", f"{bitrate * 2}k", "-g", "60", "-low_power", "1"]
        elif encoder == "h264_vaapi":
            cmd += ["-c:v", "h264_vaapi",
                    "-b:v", f"{bitrate}k", "-maxrate", f"{bitrate}k",
                    "-bufsize", f"{bitrate * 2}k", "-g", "60"]
        else:  # libx264
            p = preset if preset in _X264_VALID else "ultrafast"
            cmd += ["-c:v", "libx264", "-preset", p, "-tune", "zerolatency",
                    "-level:v", "5.1", "-crf", str(crf),
                    "-maxrate", f"{bitrate}k", "-bufsize", f"{bitrate * 2}k",
                    "-g", "60", "-keyint_min", "60",
                    "-sc_threshold", "0", "-force_key_frames", "expr:gte(t,n_forced*2)"]

        if audio_source == "all":
            for i in range(n):
                cmd += ["-map", f"{i}:a?"]
            cmd += ["-c:a", "ac3", "-af", "aresample=async=1000"]
        else:
            audio_idx = int(audio_source) if str(audio_source).isdigit() else 0
            audio_idx = max(0, min(audio_idx, n - 1))
            cmd += ["-map", f"{audio_idx}:a", "-c:a", "ac3", "-af", "aresample=async=1000"]
        cmd += _audio_metadata_args(audio_source, channel_names, n)

        cmd += [
            "-max_muxing_queue_size", "1024",
            "-mpegts_flags", "+pat_pmt_at_frames+resend_headers+initial_discontinuity",
            "-f", "mpegts", "pipe:1",
        ]
        return cmd

    def start(self) -> bool:
        if self.running:
            logger.warning("Multiview server is already running")
            return False

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.host, self.port))
            sock.close()
        except OSError as e:
            logger.error(f"Cannot bind to {self.host}:{self.port}: {e}")
            return False

        try:
            from gevent import pywsgi

            def _run():
                try:
                    self._server = pywsgi.WSGIServer(
                        (self.host, self.port),
                        self.wsgi_app,
                        log=None,
                    )
                    self.running = True
                    set_server(self)
                    self._server.serve_forever()
                except Exception as e:
                    logger.error(f"Multiview server crashed: {e}", exc_info=True)
                finally:
                    self.running = False

            import gevent as _gevent
            self._greenlet = _gevent.spawn(_run)
            return True

        except ImportError:
            logger.error("gevent is not installed; cannot start multiview server")
            return False

    def stop(self):
        if self._server:
            try:
                self._server.stop()
            except Exception:
                pass
        self.running = False
        set_server(None)
        logger.info("Multiview server stopped")

    def is_running(self) -> bool:
        return self.running
