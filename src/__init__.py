"""Dispatcharr Multiview plugin.

Tiles multiple Dispatcharr channel streams into a single MPEG-TS output
using FFmpeg. Supports multiple named layouts, each with a configurable
number of channel inputs and either an auto-grid or featured arrangement.
"""

import json
import http.client
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
    import importlib._bootstrap
    import importlib.util
    import sys
    full_name = f"{__name__}.{name}"
    # Share importlib's per-name lock with ordinary imports and other copies of
    # this package created by Dispatcharr reloads. Mark the spec as initializing
    # so ordinary imports also wait instead of returning the published module.
    with importlib._bootstrap._ModuleLockManager(full_name):
        if full_name in sys.modules:
            return sys.modules[full_name]
        spec = importlib.util.spec_from_file_location(
            full_name, os.path.join(_PLUGIN_DIR, f"{name}.py")
        )
        mod = importlib.util.module_from_spec(spec)
        # File-path loaders bypass Dispatcharr's normal guarded source loader.
        # Carry the host-provided builtins into sibling modules before execution.
        mod.__builtins__ = __builtins__
        spec._initializing = True
        try:
            sys.modules[full_name] = mod
            try:
                spec.loader.exec_module(mod)
            except BaseException:
                if sys.modules.get(full_name) is mod:
                    del sys.modules[full_name]
                raise
        finally:
            spec._initializing = False
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
        """Start the listener under Dispatcharr's leadership lease."""
        context["start_service"](
            lambda stop_event: self._run_managed_service(stop_event, context),
            name="multiview",
        )

    def _run_managed_service(self, stop_event, context):
        self._migrate_storage(context)
        srv = _server().MultiviewServer(
            host=DEFAULT_SERVER_HOST, port=DEFAULT_SERVER_PORT,
            data_dir=context["data_dir"],
            dispatch_task=context["dispatch_task"],
        )
        interval_secs = None
        next_refresh = None
        next_check = 0.0
        last_refresh_problem = None

        def ready():
            try:
                if not stop_event.is_set():
                    context["dispatch_task"]("install_pyav", {"automatic": True})
            except Exception:
                logger.exception("Could not queue the automatic PyAV check")
            finally:
                _close_db_connections()

        def tick():
            nonlocal interval_secs, next_refresh, next_check, last_refresh_problem
            now = time.monotonic()
            if now < next_check:
                return
            next_check = now + 5
            try:
                from celery.result import AsyncResult
                state = _load_submodule("refresh").advance(
                    context["data_dir"], context["dispatch_internal_task"], AsyncResult,
                    context["dispatch_scheduled_task"],
                )
                problem = (state.get("phase"), state.get("error")) if state.get("phase") in {"blocked", "error"} else None
                if problem and problem != last_refresh_problem:
                    logger.warning("Multiview refresh %s: %s", *problem)
                last_refresh_problem = problem
                hours = max(0, int(context["get_settings"]().get("epg_refresh_hours", 24)))
                current = hours * 3600
                if current != interval_secs:
                    interval_secs = current
                    next_refresh = now + current if current else None
                if next_refresh is not None and now >= next_refresh and not stop_event.is_set():
                    context["dispatch_scheduled_task"]("generate_m3u", {})
                    next_refresh = now + interval_secs
            except Exception:
                logger.exception("Multiview scheduled refresh failed")
            finally:
                _close_db_connections()

        srv.serve(stop_event, on_ready=ready, on_tick=tick)

    def _migrate_storage(self, context):
        """Copy legacy assets before changing the registered source paths."""
        from django.db import transaction
        from apps.epg.models import EPGSource
        from apps.m3u.models import M3UAccount

        data_dir = context["data_dir"]
        refresh = _load_submodule("refresh")
        try:
            with refresh.locked(data_dir):
                result = _load_submodule("storage").migrate_legacy_files(
                    _config().load_settings(), _PLUGIN_DIR, data_dir,
                )
                if result["missing_backgrounds"]:
                    logger.warning("Multiview background files are missing: %s", result["missing_backgrounds"])
                with transaction.atomic():
                    if result["files"]["multiview.m3u"] != "missing":
                        M3UAccount.objects.filter(name="Dispatcharr Multiview").update(
                            file_path=os.path.join(data_dir, "multiview.m3u"),
                        )
                    if result["files"]["multiview_epg.xml"] != "missing":
                        EPGSource.objects.filter(name="Dispatcharr Multiview").update(
                            file_path=os.path.join(data_dir, "multiview_epg.xml"),
                        )
        finally:
            _close_db_connections()

    # Dynamic fields

    def get_fields(self, context):
        """Build discovery fields using the host's durable storage path."""
        config_mod = _config()
        settings = config_mod.load_settings()
        return config_mod.build_plugin_fields(settings, context["data_dir"])

    # Action dispatcher

    def run(self, action: str, params: dict, context: dict):
        if action == "refresh_ui":
            return {"status": "success", "message": "Refreshing plugin settings", "_dispatcharr": {"refresh": "ui"}}
        if action == "generate_m3u":
            return self._generate_m3u(context, params)
        if action == "reload_active_multiviews":
            conn = http.client.HTTPConnection("127.0.0.1", DEFAULT_SERVER_PORT, timeout=5)
            try:
                conn.request("POST", "/internal/reload", body=b"{}",
                             headers={"Content-Type": "application/json"})
                response = conn.getresponse()
                if response.status != 200:
                    raise ValueError(f"server returned HTTP {response.status}")
                data = json.loads(response.read(4097))
                count = data["count"]
                if type(count) is not int or count < 0:
                    raise ValueError("invalid worker count")
            except (OSError, http.client.HTTPException, ValueError, KeyError, TypeError) as e:
                return {"status": "error", "message": f"Could not reload active multiviews: {e}"}
            finally:
                conn.close()
            return {"status": "success", "message": f"Reloading {count} active multiview(s)"}
        if action == "install_pyav":
            arch = self._deps().detect_arch()
            if not arch:
                return {"status": "error", "message": "PyAV is unavailable for this CPU architecture"}
            if params.get("automatic"):
                result = self._deps().maybe_auto_install(arch, context["data_dir"])
                if result is None:
                    return {
                        "status": "error", "message": "PyAV is current or automatic repair is not authorized"
                    }
            else:
                result = self._deps().install_pyav(arch, context["data_dir"])
            if result.get("status") == "success":
                result = {**result, "_dispatcharr": {"refresh": "plugin"}}
            return result

        return {"status": "error", "message": f"Unknown action: {action}"}

    @staticmethod
    def _deps():
        return _load_submodule("deps")

    # generate_m3u

    def _generate_m3u(self, context, params) -> dict:
        refresh = _load_submodule("refresh")
        with refresh.locked(context["data_dir"]):
            state = refresh.read_state(context["data_dir"])
            phase = state.get("phase")
            retry = phase in {"dispatching_retry", "retry_queued"} and bool(params.get("refresh_retry_token")) and params["refresh_retry_token"] == state.get("retry_token")
            if phase == "blocked":
                return {"status": "error", "message": "A refresh needs recovery; inspect the plugin logs"}
            if phase in refresh._ACTIVE and not retry:
                state["pending"] = True
                refresh.write_state(context["data_dir"], state)
                return {"status": "success", "message": "Refresh queued behind the current refresh"}
            return self._generate_m3u_locked(context, refresh, pending=bool(state.get("pending")) if retry else False)

    def _generate_m3u_locked(self, context, refresh, *, pending=False) -> dict:
        config_mod = _config()
        try:
            settings = config_mod.load_settings()
        except Exception as e:
            logger.exception("Cannot load Multiview settings for refresh")
            return {"status": "error", "message": f"Cannot load Multiview settings: {e}"}
        order = settings.get("multiview_order", [])

        m3u_content = _m3u_content(settings, order)

        m3u_path = os.path.join(context["data_dir"], "multiview.m3u")
        try:
            _load_submodule("storage").atomic_write_text(m3u_path, m3u_content)
        except OSError as e:
            return {"status": "error", "message": f"Failed to write M3U file: {e}"}

        source_id = None
        try:
            source_id = _epg().generate_epg(settings, context["data_dir"])
        except Exception as e:
            logger.warning(f"EPG generation failed: {e}")

        try:
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
            refresh.start(context["data_dir"], account.id, source_id, context["dispatch_internal_task"], pending=pending)
            return {
                "status": "success" if source_id is not None else "partial",
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
        # Dispatcharr signals the managed service after this hook returns.
        pass
