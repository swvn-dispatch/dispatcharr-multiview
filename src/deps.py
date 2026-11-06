"""On-demand install of the vendored PyAV dependency.

Dispatcharr caps plugin import size (DISPATCHARR_PLUGIN_IMPORT_MAX_BYTES, default
200MB) and one PyAV wheel per arch is ~114MB, so we do NOT ship PyAV in the plugin
zip. Instead the plugin exposes "Install PyAV" actions that fetch the matching
wheel from PyPI at runtime and unpack it under vendor/<arch>/, where
compositor_worker.py loads it.

No pip is required (the container has none): we use the PyPI JSON API + urllib.
"""

import json
import logging
import os
import platform
import shutil
import tempfile
import urllib.request
import zipfile
import fcntl
from contextlib import contextmanager

logger = logging.getLogger(__name__)

PYAV_VERSION = "18.1.0"
PY_TAG = "cp313"

def _vendor_dir(data_dir=None):
    if data_dir is None:
        from .storage import data_dir as resolve_data_dir
        data_dir = resolve_data_dir()
    return os.path.join(data_dir, "vendor")

# vendor subdir -> machine aliases + the manylinux wheel arch token
ARCHES = {
    "linux-x86_64": {"machines": ("x86_64", "amd64"), "token": "x86_64"},
    "linux-aarch64": {"machines": ("aarch64", "arm64"), "token": "aarch64"},
}


def detect_arch() -> "str | None":
    """vendor subdir for this host, or None if unsupported."""
    m = platform.machine().lower()
    for arch, info in ARCHES.items():
        if m in info["machines"]:
            return arch
    return None


def arch_dir(arch: str, data_dir=None) -> str:
    return os.path.join(_vendor_dir(data_dir), arch)


def pyav_status(arch: str, data_dir=None) -> "str | None":
    """Installed PyAV version string under vendor/<arch>, or None if absent."""
    d = arch_dir(arch, data_dir)
    if not os.path.isdir(d):
        previous = os.path.join(_vendor_dir(data_dir), f".{arch}.previous")
        if os.path.isdir(previous):
            return None
    return _pyav_status_unlocked(arch, data_dir)


def _pyav_status_unlocked(arch, data_dir=None):
    d = arch_dir(arch, data_dir)
    if not os.path.isdir(d):
        return None
    for name in os.listdir(d):
        if name.startswith("av-") and name.endswith(".dist-info"):
            return name[len("av-"):-len(".dist-info")]
    return "installed" if os.path.isdir(os.path.join(d, "av")) else None


def _get_settings() -> dict:
    try:
        from apps.plugins.models import PluginConfig
        return PluginConfig.objects.get(key="multiview").settings
    except Exception:
        return {}


def _save_settings(updates: dict):
    from apps.plugins.models import PluginConfig
    from django.db import transaction
    with transaction.atomic():
        cfg = PluginConfig.objects.select_for_update().get(key="multiview")
        for k, v in updates.items():
            if v is None:
                cfg.settings.pop(k, None)
            else:
                cfg.settings[k] = v
        cfg.save(update_fields=["settings", "updated_at"])


def _matches_wheel(fn: str, arch: str) -> bool:
    """Whether *fn* is a compatible manylinux PyAV wheel for this runtime."""
    info = ARCHES[arch]
    # PyAV 17+ distributes a cp311-abi3 wheel that is compatible with CPython
    # 3.13. Older releases used a cp313-specific wheel, so support both tags.
    python_compatible = f"-{PY_TAG}-" in fn or "-cp311-abi3-" in fn
    return (fn.endswith(".whl") and python_compatible and "manylinux" in fn
            and info["token"] in fn)


def _find_wheel(arch: str):
    """Return (url, filename) of the compatible manylinux wheel for *arch*."""
    api = f"https://pypi.org/pypi/av/{PYAV_VERSION}/json"
    with urllib.request.urlopen(api, timeout=10) as r:
        data = json.load(r)
    for f in data.get("urls", []):
        fn = f.get("filename", "")
        if _matches_wheel(fn, arch):
            return f["url"], fn
    raise RuntimeError(
        f"no {PY_TAG} manylinux {ARCHES[arch]['token']} wheel in av {PYAV_VERSION} on PyPI")


@contextmanager
def _install_lock(vendor_dir, arch):
    os.makedirs(vendor_dir, exist_ok=True)
    with open(os.path.join(vendor_dir, f".{arch}.lock"), "a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def prepare_vendor(arch, data_dir, code_dir):
    """Migrate or restore an existing vendor tree without waiting on downloads."""
    vendor_dir = _vendor_dir(data_dir)
    os.makedirs(vendor_dir, exist_ok=True)
    lock_path = os.path.join(vendor_dir, f".{arch}.lock")
    with open(lock_path, "a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        try:
            dest = arch_dir(arch, data_dir)
            previous = os.path.join(vendor_dir, f".{arch}.previous")
            if not os.path.exists(dest) and os.path.isdir(previous):
                os.rename(previous, dest)
            legacy = os.path.join(code_dir, "vendor", arch)
            if not os.path.exists(dest) and os.path.isdir(legacy):
                with tempfile.TemporaryDirectory(prefix=".migration-", dir=vendor_dir) as temporary:
                    staged = os.path.join(temporary, arch)
                    shutil.copytree(legacy, staged)
                    os.rename(staged, dest)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def install_pyav(arch: str, data_dir=None, *, automatic=False, progress=None) -> dict:
    """Download + unpack the PyAV wheel for `arch` into vendor/<arch>/."""
    if arch not in ARCHES:
        return {"status": "error", "message": f"Unsupported arch: {arch!r}"}
    vendor_dir = _vendor_dir(data_dir)
    try:
        with _install_lock(vendor_dir, arch):
            if automatic:
                if not _get_settings().get(f"pyav_consent_{arch}"):
                    return {"status": "ok", "message": "Automatic PyAV installation was not authorized"}
                previous = os.path.join(vendor_dir, f".{arch}.previous")
                if not os.path.exists(arch_dir(arch, data_dir)) and os.path.isdir(previous):
                    os.rename(previous, arch_dir(arch, data_dir))
                if _pyav_status_unlocked(arch, data_dir) == PYAV_VERSION:
                    return {"status": "ok", "message": "PyAV is already current"}
            if progress:
                progress(10, "Finding PyAV wheel")
            url, fn = _find_wheel(arch)
            with tempfile.TemporaryDirectory(prefix=".install-", dir=vendor_dir) as tmp:
                whl = os.path.join(tmp, fn)
                logger.info("multiview: downloading %s for %s", fn, arch)
                if progress:
                    progress(20, "Downloading PyAV")
                with urllib.request.urlopen(url, timeout=30) as response, open(whl, "wb") as output:
                    shutil.copyfileobj(response, output)
                staged = os.path.join(tmp, "staged")
                os.makedirs(staged)
                with zipfile.ZipFile(whl) as wheel:
                    wheel.extractall(staged)
                if not os.path.isdir(os.path.join(staged, "av")):
                    raise ValueError("Downloaded wheel does not contain PyAV")
                if progress:
                    progress(85, "Installing PyAV")
                dest = arch_dir(arch, data_dir)
                previous = os.path.join(vendor_dir, f".{arch}.previous")
                if os.path.exists(previous):
                    if not os.path.exists(dest):
                        os.rename(previous, dest)
                    else:
                        shutil.rmtree(previous)
                if os.path.exists(dest):
                    os.rename(dest, previous)
                try:
                    os.rename(staged, dest)
                except Exception:
                    if os.path.exists(previous):
                        os.rename(previous, dest)
                    raise
                if os.path.exists(previous):
                    shutil.rmtree(previous)
    except PermissionError as e:
        logger.error(f"multiview: PyAV install permission error: {e}")
        return {"status": "error", "message": (
            f"Install failed (permission denied writing {vendor_dir}). Details: {e}")}
    except Exception as e:  # noqa: BLE001
        logger.error(f"multiview: PyAV install failed: {e}", exc_info=True)
        return {"status": "error", "message": f"Install failed: {e}"}
    ver = pyav_status(arch, data_dir) or PYAV_VERSION
    msg = f"PyAV {ver} installed for {arch}."
    logger.info(f"multiview: {msg}")
    try:
        _save_settings({f"pyav_consent_{arch}": True, "pyav_auto_install_error": None})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"multiview: could not record PyAV consent: {e}")
    return {"status": "success", "message": msg}


def maybe_auto_install(arch: "str | None" = None, data_dir=None, progress=None) -> "dict | None":
    """If the user has previously consented (a prior successful 'Install
    PyAV' run for this arch) and the vendored copy is now missing or
    doesn't match the pinned PYAV_VERSION (e.g. reset by a plugin update),
    reinstall it automatically. No-op if consent was never given, or the
    install is already current. Returns the install result dict, or None
    if nothing needed to be done."""
    arch = arch or detect_arch()
    if not arch:
        return None
    settings = _get_settings()
    if not settings.get(f"pyav_consent_{arch}"):
        return None
    if pyav_status(arch, data_dir) == PYAV_VERSION:
        return None
    logger.info(f"multiview: auto-reinstalling PyAV for {arch} (consent on file)")
    result = install_pyav(arch, data_dir, automatic=True, progress=progress)
    if result.get("status") == "error":
        try:
            _save_settings({"pyav_auto_install_error": result.get("message", "unknown error")})
        except Exception:
            pass
    return result
