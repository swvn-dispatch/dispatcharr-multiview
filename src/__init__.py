"""Dispatcharr Multiview plugin.

Tiles multiple Dispatcharr channel streams into a single MPEG-TS output
using FFmpeg. Supports multiple named layouts, each with a configurable
number of channel inputs and either an auto-grid or featured arrangement.
"""

import logging
import os
import socket

from .config import (
    PLUGIN_CONFIG,
    PLUGIN_FIELDS,
    PLUGIN_DB_KEY,
    DEFAULT_SERVER_PORT,
    DEFAULT_SERVER_HOST,
    build_plugin_fields,
)
from .server import MultiviewServer, get_server, set_server

logger = logging.getLogger(__name__)


class Plugin:
    """Dispatcharr Plugin – Multiview stream tiling via FFmpeg."""

    name        = PLUGIN_CONFIG["name"]
    description = PLUGIN_CONFIG["description"]
    version     = PLUGIN_CONFIG["version"]
    author      = PLUGIN_CONFIG["author"]

    actions = [
        {
            "id": "generate_m3u",
            "label": "Regenerate M3U",
            "description": "Write multiview.m3u to the plugin folder and create/update the M3U account in Dispatcharr",
            "button_label": "Regenerate M3U",
            "button_variant": "filled",
            "button_color": "green",
        },
        {
            "id": "status",
            "label": "Status",
            "description": "Check whether the streaming server is running",
            "button_label": "Check Status",
            "button_variant": "filled",
            "button_color": "blue",
        },
    ]

    # -- Lifecycle (init) ------------------------------------------------------

    def __init__(self):
        try:
            self._autostart()
        except Exception as e:
            logger.warning(f"Multiview server auto-start skipped: {e}")

    def _autostart(self):
        existing = get_server()
        if existing and existing.is_running():
            return
        try:
            from apps.plugins.models import PluginConfig
            cfg = PluginConfig.objects.get(key=PLUGIN_DB_KEY)
            settings = cfg.settings
        except Exception:
            settings = {}
        port = int(settings.get("server_port", DEFAULT_SERVER_PORT))
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                logger.info(f"Multiview server already running on port {port}")
                return
        except OSError:
            pass
        self._start_server(settings)

    # -- Dynamic fields --------------------------------------------------------

    @property
    def fields(self):
        """Regenerate fields from current DB settings on every request."""
        try:
            from apps.plugins.models import PluginConfig
            cfg = PluginConfig.objects.get(key=PLUGIN_DB_KEY)
            settings = cfg.settings
        except Exception:
            settings = {}
        return build_plugin_fields(settings)

    # -- Action dispatcher -----------------------------------------------------

    def run(self, action: str, params: dict, context: dict):
        settings = context.get("settings", {})

        if action == "generate_m3u":
            return self._generate_m3u(settings)

        if action == "status":
            return self._status()

        return {"status": "error", "message": f"Unknown action: {action}"}

    # -- generate_m3u ----------------------------------------------------------

    def _generate_m3u(self, settings: dict) -> dict:
        port = int(settings.get("server_port", DEFAULT_SERVER_PORT))
        mv_count = max(1, int(settings.get("multiview_count", 1)))

        lines = ["#EXTM3U"]
        for n in range(1, mv_count + 1):
            name = settings.get(f"multiview_{n}_name", f"Multiview {n}") or f"Multiview {n}"
            stream_url = f"http://localhost:{port}/stream/{n}"
            lines.append(f'#EXTINF:-1 tvg-name="{name}",{name}')
            lines.append(stream_url)

        m3u_content = "\n".join(lines) + "\n"

        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        m3u_path = os.path.join(plugin_dir, "multiview.m3u")
        try:
            with open(m3u_path, "w") as f:
                f.write(m3u_content)
        except OSError as e:
            return {"status": "error", "message": f"Failed to write M3U file: {e}"}

        try:
            from apps.m3u.models import M3UAccount
            _, created = M3UAccount.objects.update_or_create(
                name="Dispatcharr Multiview",
                defaults={
                    "file_path": m3u_path,
                    "is_active": True,
                    "account_type": "STD",
                },
            )
            verb = "created" if created else "updated"
            return {
                "status": "success",
                "message": f"M3U written to {m3u_path} | M3U account {verb} in Dispatcharr",
            }
        except Exception as e:
            logger.error(f"Failed to create M3U account: {e}", exc_info=True)
            return {
                "status": "success",
                "message": f"M3U written to {m3u_path} (could not create M3U account: {e})",
            }

    # -- start_server ----------------------------------------------------------

    def _start_server(self, settings: dict) -> dict:
        existing = get_server()
        if existing and existing.is_running():
            existing.stop()

        port = int(settings.get("server_port", DEFAULT_SERVER_PORT))
        host = settings.get("server_host", DEFAULT_SERVER_HOST) or DEFAULT_SERVER_HOST

        server = MultiviewServer(host=host, port=port)
        if server.start():
            return {
                "status": "success",
                "message": f"Multiview server started on http://{host}:{port}/",
            }
        return {
            "status": "error",
            "message": f"Failed to start server on {host}:{port} — port may be in use",
        }

    # -- status ----------------------------------------------------------------

    def _status(self) -> dict:
        server = get_server()
        port = server.port if server else DEFAULT_SERVER_PORT
        try:
            from apps.plugins.models import PluginConfig
            cfg = PluginConfig.objects.get(key=PLUGIN_DB_KEY)
            port = int(cfg.settings.get("server_port", port))
        except Exception:
            pass
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return {
                    "status": "success",
                    "message": f"Server running on http://127.0.0.1:{port}/",
                    "running": True,
                }
        except OSError:
            return {"status": "success", "message": "Server is not running", "running": False}

    # -- Lifecycle -------------------------------------------------------------

    def stop(self, context: dict):
        """Called when the plugin is disabled or Dispatcharr shuts down."""
        server = get_server()
        if server and server.is_running():
            logger.info("Plugin stopping, shutting down multiview server")
            server.stop()
