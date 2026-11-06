"""Persist and advance ordered M3U and EPG refreshes without blocking workers."""

import fcntl
import json
import os
import time
from contextlib import contextmanager

from .storage import atomic_write

_TIMEOUT = 3600


@contextmanager
def locked(data_dir):
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, ".refresh.lock"), "a") as file:
        fcntl.flock(file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(file.fileno(), fcntl.LOCK_UN)


def _path(data_dir):
    return os.path.join(data_dir, "refresh-state.json")


def read_state(data_dir):
    try:
        with open(_path(data_dir), encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        return {"phase": "idle"}


def write_state(data_dir, state):
    atomic_write(_path(data_dir), json.dumps(state))


def remove_stale(state):
    from .epg import _active_tvg_ids, _remove_stale_epg_data
    from apps.epg.models import EPGSource
    source = EPGSource.objects.get(id=state["source_id"])
    _remove_stale_epg_data(source, _active_tvg_ids(state["order"]))


def layout_order_changed(state):
    from . import config
    from apps.plugins.models import PluginConfig
    current, _ = config.ensure_layout_order(PluginConfig.objects.get(key="multiview").settings)
    return current.get("multiview_order", []) != state["order"]


def start(data_dir, account_id, source_id, order, dispatch, retry_token=None):
    """Record an intent before dispatch, then persist the task ID.

    A crash between these writes is deliberately treated as ambiguous. The
    next leader will not queue EPG or another M3U refresh automatically.
    """
    state = read_state(data_dir)
    is_retry = state.get("phase") == "dispatching_retry" and retry_token and retry_token == state.get("retry_token")
    if not is_retry and state.get("phase") in {"dispatching_m3u", "m3u", "dispatching_epg", "epg", "blocked", "dispatching_retry", "retry_pending"}:
        state["pending"] = True
        write_state(data_dir, state)
        return False
    state = {"phase": "dispatching_m3u", "account_id": account_id,
             "source_id": source_id, "order": order, "started": time.time(), "pending": False}
    write_state(data_dir, state)
    task_id = dispatch("apps.m3u.tasks.refresh_single_m3u_account", args=[account_id])
    state.update(phase="m3u", task_id=task_id)
    write_state(data_dir, state)
    return True


def advance(data_dir, dispatch, dispatch_plugin, result_for, validate_m3u):
    """Advance one recorded phase, with no blocking task-result wait."""
    with locked(data_dir):
        state = read_state(data_dir)
        phase = state.get("phase")
        if phase == "retry_pending":
            import uuid
            token = uuid.uuid4().hex
            state.update(phase="dispatching_retry", started=time.time(), pending=False, retry_token=token)
            write_state(data_dir, state)
            try:
                dispatch_plugin("generate_m3u", {"retry_token": token})
            except Exception:
                state.update(phase="retry_pending", pending=True)
                write_state(data_dir, state)
                raise
            return state
        if phase not in {"dispatching_m3u", "m3u", "dispatching_epg", "epg", "dispatching_retry"}:
            return state
        if phase.startswith("dispatching_"):
            if time.time() - state.get("started", 0) > _TIMEOUT:
                state.update(phase="blocked", blocked_phase=phase, error="Refresh dispatch outcome is unknown; inspect Dispatcharr tasks before recovery")
                write_state(data_dir, state)
            return state
        task = result_for(state["task_id"])
        if not task.ready():
            if time.time() - state.get("started", 0) > _TIMEOUT:
                state.update(phase="blocked", blocked_phase=phase, error="Refresh timed out; inspect Dispatcharr tasks before recovery")
                write_state(data_dir, state)
            return state
        if not task.successful():
            state.update(phase="error", error=f"{phase.upper()} refresh task failed")
        elif phase == "m3u":
            error = validate_m3u(state, task.result)
            if error:
                state.update(phase="error", error=error)
            elif state.get("pending"):
                state["phase"] = "retry_pending"
                write_state(data_dir, state)
                return state
            elif state.get("source_id") is None:
                state["phase"] = "complete"
            else:
                if layout_order_changed(state):
                    state.update(phase="retry_pending", pending=True)
                    write_state(data_dir, state)
                    return state
                remove_stale(state)
                state.update(phase="dispatching_epg", started=time.time())
                state.pop("task_id", None)
                write_state(data_dir, state)
                task_id = dispatch("apps.epg.tasks.refresh_epg_data", args=[state["source_id"]])
                state.update(phase="epg", task_id=task_id)
        else:
            error = validate_epg(state)
            if error:
                state.update(phase="blocked", blocked_phase="epg", error=error)
            else:
                state["phase"] = "complete"
        write_state(data_dir, state)
        if state["phase"] == "complete" and state.get("pending"):
            state["phase"] = "retry_pending"
            write_state(data_dir, state)
        return state


def validate_m3u(state, result):
    """Reject skipped/failed runs and verify this run updated expected streams."""
    if result != "Dispatched jobs complete.":
        return f"M3U refresh did not complete successfully: {result}"
    from datetime import datetime, timezone
    from apps.m3u.models import M3UAccount
    from apps.channels.models import Stream

    account = M3UAccount.objects.get(id=state["account_id"])
    if account.status != M3UAccount.Status.SUCCESS or not account.updated_at:
        return "M3U account did not report a successful refresh"
    if account.updated_at < datetime.fromtimestamp(state["started"], tz=timezone.utc):
        return "M3U account success predates this refresh"
    ids = {f"mv-{layout_id}" for layout_id in state["order"]}
    started = datetime.fromtimestamp(state["started"], tz=timezone.utc)
    found = set(Stream.objects.filter(m3u_account_id=account.id, tvg_id__in=ids, is_stale=False, last_seen__gte=started).values_list("tvg_id", flat=True))
    if found != ids:
        return "M3U refresh did not create every expected layout stream"
    if state.get("source_id") is not None:
        from apps.channels.models import Channel
        from apps.channels.managers import epg_ids_mapped_to_channels
        from apps.epg.models import EPGData
        used = set(Channel.objects.filter(streams__m3u_account_id=account.id, streams__tvg_id__in=ids).values_list("streams__tvg_id", flat=True))
        mapped = epg_ids_mapped_to_channels(epg_source_id=state["source_id"])
        existing = set(EPGData.objects.filter(epg_source_id=state["source_id"], tvg_id__in=used, id__in=mapped).values_list("tvg_id", flat=True))
        if existing != used:
            return "Some Multiview channels are not mapped to their generated EPG entries"
    return None


def validate_epg(state):
    from datetime import datetime, timezone
    from apps.epg.models import EPGSource

    source = EPGSource.objects.get(id=state["source_id"])
    if source.status != EPGSource.STATUS_SUCCESS or not source.updated_at:
        return "EPG source did not report a successful refresh"
    if source.updated_at < datetime.fromtimestamp(state["started"], tz=timezone.utc):
        return "EPG source success predates this refresh"
    return None


def warn_if_blocked(data_dir):
    """Make failed background work visible on the native plugin settings page."""
    try:
        state = read_state(data_dir)
    except FileNotFoundError:
        return None
    if state.get("phase") not in {"error", "blocked"}:
        return None
    return {"id": "_warn_refresh", "label": "M3U/EPG refresh needs attention",
            "type": "info", "description": state.get("error", "Refresh failed")}


def recover(data_dir, result_for):
    """Clear a blocked M3U state only after a known task is terminal."""
    with locked(data_dir):
        state = read_state(data_dir)
        if state.get("phase") != "blocked":
            return False, "No blocked refresh"
        task_id = state.get("task_id")
        if state.get("blocked_phase", "").startswith("dispatching_") or not task_id:
            return False, "Task submission is ambiguous. Check Dispatcharr task history before clearing refresh-state.json."
        if state.get("blocked_phase") == "epg":
            return False, "EPG refresh may have deferred work. Check Dispatcharr tasks before clearing refresh-state.json."
        task = result_for(task_id)
        if not task.ready():
            return False, "The previous refresh task has not reached a terminal state"
        state.update(phase="error", error="Previous refresh ended; retry the refresh")
        write_state(data_dir, state)
        return True, "Previous refresh ended. Retry the refresh."
