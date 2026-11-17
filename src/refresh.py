"""Coordinate approved Dispatcharr refresh tasks without blocking a worker."""

from contextlib import contextmanager
import fcntl
import json
import os
import time

from .storage import atomic_write_text

_ACTIVE = {"dispatching_m3u", "m3u", "dispatching_epg", "epg", "blocked"}
_TIMEOUT = 3600


@contextmanager
def locked(data_dir):
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, ".refresh.lock"), "a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def read_state(data_dir):
    try:
        with open(os.path.join(data_dir, "refresh-state.json"), encoding="utf-8") as state:
            return json.load(state)
    except FileNotFoundError:
        return {"phase": "idle"}


def write_state(data_dir, state):
    atomic_write_text(os.path.join(data_dir, "refresh-state.json"), json.dumps(state))


def start(data_dir, account_id, source_id, dispatch):
    """Record intent before enqueueing; caller must hold the refresh lock."""
    state = {"phase": "dispatching_m3u", "started": time.time(), "source_id": source_id}
    write_state(data_dir, state)
    task_id = dispatch("apps.m3u.tasks.refresh_single_m3u_account", args=[account_id])
    state.update(phase="m3u", task_id=task_id)
    write_state(data_dir, state)


def advance(data_dir, dispatch, result_for):
    """Poll one task and dispatch EPG only after a confirmed M3U success."""
    with locked(data_dir):
        state = read_state(data_dir)
        phase = state.get("phase")
        if phase not in {"dispatching_m3u", "m3u", "dispatching_epg", "epg"}:
            return state
        if phase.startswith("dispatching_"):
            if time.time() - state.get("started", 0) > _TIMEOUT:
                state.update(phase="blocked", error="Task submission outcome is unknown; inspect Dispatcharr tasks")
                write_state(data_dir, state)
            return state
        task = result_for(state["task_id"])
        if not task.ready():
            if time.time() - state.get("started", 0) > _TIMEOUT:
                state.update(phase="blocked", error="Refresh task timed out; inspect Dispatcharr tasks")
                write_state(data_dir, state)
            return state
        if not task.successful():
            state.update(phase="error", error=f"{phase.upper()} refresh task failed")
        elif phase == "m3u" and task.result != "Dispatched jobs complete.":
            state.update(phase="error", error=f"M3U refresh did not complete: {task.result}")
        elif phase == "m3u" and state.get("source_id") is not None:
            state.update(phase="dispatching_epg", started=time.time())
            state.pop("task_id", None)
            write_state(data_dir, state)
            task_id = dispatch("apps.epg.tasks.refresh_epg_data", args=[state["source_id"]])
            state.update(phase="epg", task_id=task_id)
        else:
            state["phase"] = "complete"
        write_state(data_dir, state)
        return state
