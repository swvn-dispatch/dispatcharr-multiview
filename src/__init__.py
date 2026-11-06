"""Dispatcharr Multiview plugin.

Tiles multiple Dispatcharr channel streams into a single MPEG-TS output
using FFmpeg. Supports multiple named layouts, each with a configurable
number of channel inputs and either an auto-grid or featured arrangement.
"""

import json
import logging
import os
import time

logger = logging.getLogger(__name__)

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(_PLUGIN_DIR, "plugin.json")) as _f:
    _PLUGIN_CONFIG = json.load(_f)

PLUGIN_DB_KEY = "multiview"
DEFAULT_SERVER_PORT = 9292
DEFAULT_SERVER_HOST = "0.0.0.0"


def _load_submodule(name: str):
    """Load a sibling module by file path.

    importlib.import_module with a relative package requires the parent to be
    in sys.modules. During Dispatcharr reload cycles that entry may be absent,
    producing KeyError or ModuleNotFoundError. Loading by file path sidesteps
    that lookup entirely.
    """
    import importlib.util
    import sys
    full_name = f"{__name__}.{name}"
    if full_name in sys.modules:
        return sys.modules[full_name]
    spec = importlib.util.spec_from_file_location(
        full_name, os.path.join(_PLUGIN_DIR, f"{name}.py")
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _m3u_content(settings: dict, order: list) -> str:
    """Build the playlist with numbering that follows the persisted layout order."""
    lines = ["#EXTM3U"]
    for number, layout_id in enumerate(order, start=1):
        name = settings.get(f"multiview_{layout_id}_name", f"Multiview {layout_id}") or f"Multiview {layout_id}"
        safe_name = name.replace('"', "'")  # quotes break EXTINF attribute parsing
        stream_url = f"http://127.0.0.1:{DEFAULT_SERVER_PORT}/stream/{layout_id}"
        lines.append(
            f'#EXTINF:-1 tvg-id="mv-{layout_id}" tvg-chno="{number}" '
            f'tvg-name="{safe_name}",{safe_name}'
        )
        lines.append(stream_url)
    return "\n".join(lines) + "\n"


def _config():
    return _load_submodule("config")


def _server():
    return _load_submodule("server")


def _epg():
    return _load_submodule("epg")


def _close_db_connections():
    """Release Django DB connections opened outside Django's own request cycle.

    Background threads/loops here never go through Django's request_finished
    signal, so connections they open are never cleaned up on their own.
    """
    try:
        from django.db import close_old_connections
        close_old_connections()
    except Exception:
        pass


class Plugin:
    """Dispatcharr Plugin: Multiview stream tiling via FFmpeg."""

    name        = _PLUGIN_CONFIG["name"]
    description = _PLUGIN_CONFIG["description"]
    version     = _PLUGIN_CONFIG["version"]
    author      = _PLUGIN_CONFIG["author"]

    actions = _PLUGIN_CONFIG["actions"]

    def on_leader_acquired(self, context):
        context["start_service"](lambda stop_event: self._run_service(stop_event, context), name="multiview")

    def on_leader_lost(self, context):
        # Dispatcharr has already signalled and joined the managed service.
        pass

    def _run_service(self, stop_event, context):
        server = _server().MultiviewServer(
            host=DEFAULT_SERVER_HOST, port=DEFAULT_SERVER_PORT,
            data_dir=context["data_dir"], dispatch_task=context["dispatch_task"],
        )
        server.refresh_module = _load_submodule("refresh")
        server.storage_module = _load_submodule("storage")
        state = {"next_check": 0.0, "next_refresh": None, "interval": None}

        def ready():
            if stop_event.is_set():
                return
            from .control import ensure_token
            ensure_token(context["data_dir"])
            self._migrate_backgrounds(context["data_dir"])
            if stop_event.is_set():
                return
            arch = self._deps().detect_arch()
            if arch:
                action = {"linux-x86_64": "install_pyav_amd64", "linux-aarch64": "install_pyav_arm64"}.get(arch)
                try:
                    settings = context["get_settings"]()
                    if action and (settings.get(f"pyav_consent_{arch}") or os.path.isdir(os.path.join(_PLUGIN_DIR, "vendor", arch))):
                        context["dispatch_task"](action, {"automatic": True})
                except Exception:
                    logger.exception("Could not queue automatic PyAV repair")

        def tick():
            now = time.monotonic()
            if now < state["next_check"]:
                return
            state["next_check"] = now + 5
            try:
                from celery.result import AsyncResult
                from . import refresh
                def scheduled_dispatch(action, params):
                    if stop_event.is_set():
                        raise RuntimeError("Multiview leadership is stopping")
                    return context["dispatch_scheduled_task"](action, params)

                def internal_dispatch(action, args):
                    if stop_event.is_set():
                        raise RuntimeError("Multiview leadership is stopping")
                    return context["dispatch_internal_task"](action, args=args)

                refresh.advance(context["data_dir"], internal_dispatch,
                                scheduled_dispatch, AsyncResult, refresh.validate_m3u)
                interval = max(0, int(context["get_settings"]().get("epg_refresh_hours", 24))) * 3600
                if interval != state["interval"]:
                    state["interval"] = interval
                    state["next_refresh"] = now + interval if interval else None
                if state["next_refresh"] is not None and now >= state["next_refresh"]:
                    scheduled_dispatch("generate_m3u", {})
                    state["next_refresh"] = now + interval
            except Exception:
                logger.exception("Multiview service scheduling failed")
            finally:
                _close_db_connections()

        server.serve(stop_event, on_ready=ready, on_tick=tick)

    def _migrate_backgrounds(self, data_dir):
        from apps.plugins.models import PluginConfig
        from django.db import transaction
        from .storage import migrate_backgrounds
        with transaction.atomic():
            cfg = PluginConfig.objects.select_for_update().get(key=PLUGIN_DB_KEY)
            settings, changed = migrate_backgrounds(cfg.settings, _PLUGIN_DIR, data_dir)
            if changed:
                cfg.settings = settings
                cfg.save(update_fields=["settings", "updated_at"])

    # Dynamic fields

    @property
    def fields(self):
        """Regenerate fields from current DB settings on every request."""
        config_mod = _config()
        try:
            from apps.plugins.models import PluginConfig
            cfg = PluginConfig.objects.get(key=PLUGIN_DB_KEY)
            settings, changed1 = config_mod.ensure_layout_order(cfg.settings)
            settings, changed2 = config_mod.reconcile_layout_count(settings)
            settings, changed3 = config_mod.ensure_custom_layout_order(settings)
            if changed1 or changed2 or changed3:
                cfg.settings = settings
                cfg.save()
        except Exception:
            settings, _changed = config_mod.ensure_layout_order({})
            settings, _changed = config_mod.reconcile_layout_count(settings)
            settings, _changed = config_mod.ensure_custom_layout_order(settings)
        return config_mod.build_plugin_fields(settings)

    # Action dispatcher

    def run(self, action: str, params: dict, context: dict):
        if action == "generate_m3u":
            return self._generate_m3u(context, params)
        if action == "reload_active_multiviews":
            from .control import reload_streams
            try:
                count = reload_streams(context["data_dir"], DEFAULT_SERVER_PORT)
                return {"status": "success", "message": f"Reloading {count} active multiview(s)"}
            except Exception as exc:
                raise RuntimeError(f"Multiview service unavailable: {exc}") from exc
        if action == "install_pyav_amd64":
            if params.get("automatic"):
                self._deps().prepare_vendor("linux-x86_64", context["data_dir"], _PLUGIN_DIR)
                return self._deps().maybe_auto_install("linux-x86_64", context["data_dir"], context.get("report_progress")) or {"status": "ok", "message": "PyAV is current or automatic repair is not authorized"}
            return self._deps().install_pyav("linux-x86_64", context["data_dir"], progress=context.get("report_progress"))
        if action == "install_pyav_arm64":
            if params.get("automatic"):
                self._deps().prepare_vendor("linux-aarch64", context["data_dir"], _PLUGIN_DIR)
                return self._deps().maybe_auto_install("linux-aarch64", context["data_dir"], context.get("report_progress")) or {"status": "ok", "message": "PyAV is current or automatic repair is not authorized"}
            return self._deps().install_pyav("linux-aarch64", context["data_dir"], progress=context.get("report_progress"))

        return {"status": "error", "message": f"Unknown action: {action}"}

    @staticmethod
    def _deps():
        return _load_submodule("deps")

    # generate_m3u

    def _generate_m3u(self, context, params=None) -> dict:
        from . import refresh
        with refresh.locked(context["data_dir"]):
            state = refresh.read_state(context["data_dir"])
            retry = state.get("phase") == "dispatching_retry" and params and params.get("retry_token") == state.get("retry_token")
            if state.get("phase") == "blocked":
                return {"status": "error", "message": state.get("error", "A prior refresh requires recovery")}
            if not retry and state.get("phase") in {"dispatching_m3u", "m3u", "dispatching_epg", "epg", "retry_pending", "dispatching_retry"}:
                state["pending"] = True
                refresh.write_state(context["data_dir"], state)
                return {"status": "ok", "message": "Refresh queued behind the current refresh"}
            return self._generate_m3u_locked(context, params)

    def _generate_m3u_locked(self, context, params=None) -> dict:
        from . import refresh
        config_mod = _config()
        try:
            from django.db import transaction
            from apps.plugins.models import PluginConfig
            with transaction.atomic():
                cfg = PluginConfig.objects.select_for_update().get(key=PLUGIN_DB_KEY)
                settings, changed1 = config_mod.ensure_layout_order(cfg.settings)
                settings, changed2 = config_mod.reconcile_layout_count(settings)
                settings, changed3 = config_mod.ensure_custom_layout_order(settings)
                if changed1 or changed2 or changed3:
                    cfg.settings = settings
                    cfg.save(update_fields=["settings", "updated_at"])
        except Exception as exc:
            logger.exception("Cannot load Multiview settings for M3U generation")
            return {"status": "error", "message": f"Cannot load Multiview settings: {exc}"}
        order = settings.get("multiview_order", [])

        m3u_content = _m3u_content(settings, order)

        from .storage import atomic_write
        m3u_path = os.path.join(context["data_dir"], "multiview.m3u")
        try:
            if context.get("report_progress"):
                context["report_progress"](10, "Generating playlist and guide")
            atomic_write(m3u_path, m3u_content)
        except OSError as e:
            return {"status": "error", "message": f"Failed to write M3U file: {e}"}

        source_id = None
        try:
            source_id = _epg().generate_epg(settings, context["data_dir"])
        except Exception as e:
            logger.warning(f"EPG generation failed: {e}")

        try:
            if context.get("report_progress"):
                context["report_progress"](70, "Registering playlist")
            from apps.m3u.models import M3UAccount
            account, created = M3UAccount.objects.update_or_create(
                name="Dispatcharr Multiview",
                defaults={
                    "file_path": m3u_path,
                    "is_active": True,
                    "account_type": "STD",
                    "refresh_interval": 0,
                },
            )
            verb = "created" if created else "updated"
            refresh.start(context["data_dir"], account.id, source_id, order,
                          context["dispatch_internal_task"], (params or {}).get("retry_token"))
            return {
                "status": "ok" if source_id is not None else "partial",
                "message": f"M3U account {verb}; Dispatcharr refresh queued" + (" (EPG generation failed)" if source_id is None else ""),
            }
        except Exception as e:
            logger.error(f"Failed to create M3U account: {e}", exc_info=True)
            return {
                "status": "error",
                "message": f"M3U written to {m3u_path} (could not create M3U account: {e})",
            }

    # Lifecycle

    def stop(self, context: dict):
        """Called when the plugin is disabled or Dispatcharr shuts down."""
        # The host owns the service and signals it after this hook returns.
        pass
