"""Multiview streaming server.

Runs a lightweight gevent WSGI server. Each /stream/{n} request spawns an
ffmpeg subprocess that tiles the configured channels and pipes MPEG-TS output
back to the client. The subprocess is killed when the client disconnects.
Zero ffmpeg processes run when nobody is watching.

Each channel is opened via Dispatcharr's ProxyServer internal API so that
full fallback/profile behaviour is respected and connections appear in the
Dispatcharr stats view with user-agent "multiview-plugin".

Routes:
  GET /health              Health check
  GET /stream/{n}          MPEG-TS multiview stream for layout n (1-based)
  GET /internal/ch/{uuid}  Internal per-channel TS feed consumed by ffmpeg
"""

import logging
import math
import os
import socket
import subprocess
import threading
import uuid as _uuid_module

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
        parts[-1] = "[main][s1]hstack=inputs=2[v]"
    else:
        side_inputs = "".join(f"[s{i}]" for i in range(1, n))
        parts.append(f"{side_inputs}vstack=inputs={side_count}[right]")
        parts.append("[main][right]hstack=inputs=2[v]")

    filter_complex = "; ".join(parts)
    return filter_complex, ["-map", "[v]", "-map", "0:a"]


def _build_ffmpeg_cmd(input_urls: list[str], layout: str) -> list[str]:
    n = len(input_urls)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"]

    for url in input_urls:
        cmd += ["-i", url]

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

        if path.startswith("/internal/ch/"):
            channel_id = path[len("/internal/ch/"):]
            return self._serve_channel_internal(channel_id, start_response)

        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"Not Found\n"]

    def _serve_stream(self, n: int, start_response):
        logger.info(f"Stream request: layout {n}")
        try:
            input_urls, layout = self._resolve_layout(n)
        except LookupError as e:
            logger.warning(f"Layout {n} not ready: {e}")
            start_response("404 Not Found", [("Content-Type", "text/plain")])
            return [str(e).encode()]
        except Exception as e:
            logger.error(f"Error resolving layout {n}: {e}", exc_info=True)
            start_response("500 Internal Server Error", [("Content-Type", "text/plain")])
            return [b"Server error\n"]

        cmd = _build_ffmpeg_cmd(input_urls, layout)
        logger.info(
            f"Starting ffmpeg: layout={n} inputs={len(input_urls)} style={layout} "
            f"urls={input_urls}"
        )

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        def _log_stderr():
            try:
                for raw in proc.stderr:
                    line = raw.decode("utf-8", errors="replace").rstrip()
                    if line:
                        logger.warning(f"ffmpeg layout={n}: {line}")
            except Exception:
                pass

        threading.Thread(target=_log_stderr, daemon=True, name=f"ffmpeg-stderr-{n}").start()

        start_response("200 OK", [
            ("Content-Type", "video/mp2t"),
            ("Cache-Control", "no-cache"),
            ("Transfer-Encoding", "chunked"),
        ])

        def stream_gen():
            bytes_sent = 0
            try:
                while True:
                    chunk = proc.stdout.read(65536)
                    if not chunk:
                        break
                    bytes_sent += len(chunk)
                    yield chunk
            finally:
                try:
                    proc.kill()
                    proc.wait()
                except Exception:
                    pass
                logger.info(f"ffmpeg layout={n} terminated after {bytes_sent:,} bytes")

        return stream_gen()

    def _serve_channel_internal(self, channel_id: str, start_response):
        """Serve a single channel's TS stream via Dispatcharr's ProxyServer.

        This is the internal endpoint that ffmpeg calls as an input. It opens
        the channel through Dispatcharr's proxy infrastructure (full fallback,
        stream profiles, stats) without going through the HTTP proxy endpoint
        (which may be behind a network-access CIDR check).
        """
        try:
            _uuid_module.UUID(channel_id)
        except ValueError:
            start_response("400 Bad Request", [("Content-Type", "text/plain")])
            return [b"Invalid channel UUID\n"]

        logger.info(f"Internal channel request: {channel_id}")

        try:
            from apps.proxy.live_proxy.server import ProxyServer
            from apps.proxy.live_proxy.services.channel_service import ChannelService
            from apps.proxy.live_proxy.url_utils import generate_stream_url
            from apps.proxy.live_proxy.output.ts.generator import StreamGenerator
            from apps.channels.models import Channel
        except ImportError as e:
            logger.error(f"Import error in _serve_channel_internal: {e}")
            start_response("500 Internal Server Error", [("Content-Type", "text/plain")])
            return [b"Server error\n"]

        try:
            proxy_server = ProxyServer.get_instance()
        except Exception as e:
            logger.error(f"Could not get ProxyServer instance: {e}")
            start_response("503 Service Unavailable", [("Content-Type", "text/plain")])
            return [b"Proxy server unavailable\n"]

        channel_initializing = False

        if not proxy_server.check_if_channel_exists(channel_id):
            stream_url, stream_ua, transcode, profile_value = generate_stream_url(channel_id)
            if not stream_url:
                logger.warning(f"No stream available for channel {channel_id}")
                start_response("503 Service Unavailable", [("Content-Type", "text/plain")])
                return [b"No stream available for this channel\n"]

            # generate_stream_url set channel_stream:{ch.id} and stream_profile:{stream_id}
            # in Redis — read them back so initialize_channel can record the full context
            stream_id = None
            m3u_profile_id = None
            if proxy_server.redis_client:
                try:
                    ch = Channel.objects.get(uuid=channel_id)
                    raw = proxy_server.redis_client.get(f"channel_stream:{ch.id}")
                    if raw:
                        stream_id = int(raw)
                        raw2 = proxy_server.redis_client.get(f"stream_profile:{stream_id}")
                        if raw2:
                            m3u_profile_id = int(raw2)
                except Exception as e:
                    logger.warning(f"Could not read stream assignment from Redis: {e}")

            success = ChannelService.initialize_channel(
                channel_id,
                stream_url,
                stream_ua,
                transcode,
                profile_value,
                stream_id,
                m3u_profile_id,
            )
            if not success:
                logger.error(f"Failed to initialize channel {channel_id}")
                start_response("503 Service Unavailable", [("Content-Type", "text/plain")])
                return [b"Failed to initialize channel\n"]

            channel_initializing = True
        else:
            # Channel already running — ensure this worker has a local buffer and
            # client_manager (the channel may be owned by a different worker)
            proxy_server.initialize_channel(None, channel_id, None)

        # Wait for the buffer to be set up (initialize_channel creates it synchronously
        # but guard in case of a race with channel teardown)
        try:
            import gevent
            _sleep = gevent.sleep
        except ImportError:
            import time
            _sleep = time.sleep

        source_buffer = None
        for _ in range(30):
            source_buffer = proxy_server.get_buffer(channel_id)
            if source_buffer is not None:
                break
            _sleep(0.2)

        if source_buffer is None:
            logger.warning(f"Buffer not available for channel {channel_id}")
            start_response("503 Service Unavailable", [("Content-Type", "text/plain")])
            return [b"Stream buffer unavailable\n"]

        for _ in range(15):
            if channel_id in proxy_server.client_managers:
                break
            _sleep(0.2)

        if channel_id not in proxy_server.client_managers:
            logger.warning(f"Client manager not available for channel {channel_id}")
            start_response("503 Service Unavailable", [("Content-Type", "text/plain")])
            return [b"Client manager unavailable\n"]

        client_id = str(_uuid_module.uuid4())
        proxy_server.client_managers[channel_id].add_client(
            client_id, "127.0.0.1", "multiview-plugin", None, "mpegts", None,
        )
        logger.info(f"Registered multiview client {client_id} for channel {channel_id}")

        start_response("200 OK", [
            ("Content-Type", "video/mp2t"),
            ("Cache-Control", "no-cache"),
            ("Transfer-Encoding", "chunked"),
        ])

        def stream_gen():
            gen = StreamGenerator(
                channel_id=channel_id,
                client_id=client_id,
                client_ip="127.0.0.1",
                client_user_agent="multiview-plugin",
                channel_initializing=channel_initializing,
                buffer=source_buffer,
            )
            yield from gen.generate()

        return stream_gen()

    def _resolve_layout(self, n: int) -> tuple[list[str], str]:
        """Return ([internal_channel_urls], layout_name) for layout n."""
        from apps.plugins.models import PluginConfig
        from apps.channels.models import Channel
        from .config import PLUGIN_DB_KEY

        try:
            cfg = PluginConfig.objects.get(key=PLUGIN_DB_KEY)
            settings = cfg.settings
        except Exception:
            settings = {}

        ch_count = max(2, int(settings.get(f"multiview_{n}_channel_count", 4)))
        layout = settings.get(f"multiview_{n}_layout", "auto")

        logger.info(f"Resolving layout {n}: ch_count={ch_count} style={layout}")
        input_urls = []

        for m in range(1, ch_count + 1):
            ch_id_str = settings.get(f"multiview_{n}_channel_{m}", "_none")
            if not ch_id_str or ch_id_str == "_none":
                raise LookupError(f"Layout {n} channel {m} is not configured")
            try:
                ch = Channel.objects.get(id=int(ch_id_str))
            except Channel.DoesNotExist:
                raise LookupError(f"Channel id={ch_id_str} not found")

            url = f"http://127.0.0.1:{self.port}/internal/ch/{ch.uuid}"
            logger.info(f"  channel {m}: id={ch_id_str} name={ch.name!r} url={url}")
            input_urls.append(url)

        return input_urls, layout

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
