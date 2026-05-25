"""Multiview streaming server.

Runs a lightweight gevent WSGI server. Each /stream/{n} request spawns an
ffmpeg subprocess that tiles the configured channels and pipes MPEG-TS output
back to the client. The subprocess is killed when the client disconnects.
Zero ffmpeg processes run when nobody is watching.

Routes:
  GET /health      Health check
  GET /stream/{n}  MPEG-TS multiview stream for layout n (1-based)
"""

import logging
import math
import os
import socket
import subprocess
import threading

logger = logging.getLogger(__name__)

_server_instance = None


def get_server() -> "MultiviewServer | None":
    return _server_instance


def set_server(s):
    global _server_instance
    _server_instance = s


# ── Layout helpers ────────────────────────────────────────────────────────────

def _auto_grid_filter(n: int) -> tuple[str, list[str]]:
    """
    Return (filter_complex, output_map_args) for an n-input square-ish grid.
    All inputs are scaled to tile_w × tile_h then assembled with xstack.
    Output resolution is 1920 × 1080.
    """
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    tile_w = 1920 // cols
    tile_h = 1080 // rows

    scale_parts = [f"[{i}:v]scale={tile_w}:{tile_h}[v{i}]" for i in range(n)]

    positions = []
    for i in range(n):
        c = i % cols
        r = i // cols
        x = "0" if c == 0 else ("w0" if c == 1 else f"{c}*w0")
        y = "0" if r == 0 else ("h0" if r == 1 else f"{r}*h0")
        positions.append(f"{x}_{y}")

    inputs_str = "".join(f"[v{i}]" for i in range(n))
    xstack = f"{inputs_str}xstack=inputs={n}:layout={'|'.join(positions)}[v]"

    filter_complex = "; ".join(scale_parts) + "; " + xstack
    return filter_complex, ["-map", "[v]", "-map", "0:a"]


def _featured_filter(n: int) -> tuple[str, list[str]]:
    """
    Return (filter_complex, output_map_args) for featured layout:
    channel 0 fills the left 2/3 of a 1920×1080 frame;
    channels 1..n-1 are stacked vertically in the right 1/3.
    """
    main_w, main_h = 1280, 1080
    side_w = 640
    side_count = n - 1
    side_h = 1080 // side_count if side_count > 0 else 1080

    parts = [f"[0:v]scale={main_w}:{main_h}[main]"]
    for i in range(1, n):
        parts.append(f"[{i}:v]scale={side_w}:{side_h}[s{i}]")

    if side_count == 1:
        parts.append("[s1][main]hstack=inputs=2[v]")
        # swap order so main is on left
        parts[-1] = "[main][s1]hstack=inputs=2[v]"
    else:
        side_inputs = "".join(f"[s{i}]" for i in range(1, n))
        parts.append(f"{side_inputs}vstack=inputs={side_count}[right]")
        parts.append("[main][right]hstack=inputs=2[v]")

    filter_complex = "; ".join(parts)
    return filter_complex, ["-map", "[v]", "-map", "0:a"]


def _build_ffmpeg_cmd(
    input_urls: list[str],
    layout: str,
    dispatcharr_base_url: str,
) -> list[str]:
    n = len(input_urls)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"]

    for url in input_urls:
        cmd += ["-user_agent", "multiview-plugin", "-i", url]

    if layout == "featured":
        filter_complex, map_args = _featured_filter(n)
    else:
        filter_complex, map_args = _auto_grid_filter(n)

    cmd += ["-filter_complex", filter_complex]
    cmd += map_args
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency"]
    cmd += ["-c:a", "aac", "-b:a", "128k"]
    cmd += ["-f", "mpegts", "pipe:1"]
    return cmd


# ── Server ────────────────────────────────────────────────────────────────────

class MultiviewServer:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._server = None
        self._thread = None
        self.running = False

    # -- WSGI ------------------------------------------------------------------

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

        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"Not Found\n"]

    def _serve_stream(self, n: int, start_response):
        try:
            settings, input_urls, layout = self._resolve_layout(n)
        except LookupError as e:
            start_response("404 Not Found", [("Content-Type", "text/plain")])
            return [str(e).encode()]
        except Exception as e:
            logger.error(f"Error resolving layout {n}: {e}", exc_info=True)
            start_response("500 Internal Server Error", [("Content-Type", "text/plain")])
            return [b"Server error\n"]

        dispatcharr_url = settings.get("dispatcharr_base_url", "http://localhost:9191")
        cmd = _build_ffmpeg_cmd(input_urls, layout, dispatcharr_url)
        logger.info(f"Starting ffmpeg for layout {n} with {len(input_urls)} inputs")

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

        start_response("200 OK", [
            ("Content-Type", "video/mp2t"),
            ("Cache-Control", "no-cache"),
            ("Transfer-Encoding", "chunked"),
        ])

        def stream_gen():
            try:
                while True:
                    chunk = proc.stdout.read(65536)
                    if not chunk:
                        break
                    yield chunk
            finally:
                try:
                    proc.kill()
                    proc.wait()
                except Exception:
                    pass
                logger.info(f"ffmpeg for layout {n} terminated")

        return stream_gen()

    def _resolve_layout(self, n: int) -> tuple[dict, list[str], str]:
        """Return (settings, [stream_urls], layout_name) for layout n."""
        from apps.plugins.models import PluginConfig
        from apps.channels.models import Channel
        from .config import PLUGIN_DB_KEY, DEFAULT_DISPATCHARR_URL

        try:
            cfg = PluginConfig.objects.get(key=PLUGIN_DB_KEY)
            settings = cfg.settings
        except Exception:
            settings = {}

        dispatcharr_url = settings.get("dispatcharr_base_url", DEFAULT_DISPATCHARR_URL).rstrip("/")
        ch_count = max(2, int(settings.get(f"multiview_{n}_channel_count", 4)))
        layout = settings.get(f"multiview_{n}_layout", "auto")

        input_urls = []
        for m in range(1, ch_count + 1):
            ch_id = settings.get(f"multiview_{n}_channel_{m}", "_none")
            if not ch_id or ch_id == "_none":
                raise LookupError(f"Layout {n} channel {m} is not configured")
            try:
                ch = Channel.objects.get(id=int(ch_id))
            except Channel.DoesNotExist:
                raise LookupError(f"Channel id={ch_id} not found")
            input_urls.append(f"{dispatcharr_url}/proxy/ts/stream/{ch.uuid}")

        return settings, input_urls, layout

    # -- Lifecycle -------------------------------------------------------------

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
                    logger.info(f"Multiview server started on http://{self.host}:{self.port}/")
                    self._server.serve_forever()
                except Exception as e:
                    logger.error(f"Multiview server crashed: {e}", exc_info=True)
                finally:
                    self.running = False

            self._thread = threading.Thread(target=_run, daemon=True, name="multiview-server")
            self._thread.start()
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
