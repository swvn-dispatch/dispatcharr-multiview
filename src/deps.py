"""On-demand install of the vendored PyAV dependency.

Dispatcharr caps plugin import size (DISPATCHARR_PLUGIN_IMPORT_MAX_BYTES, default
200MB) and one PyAV wheel per arch is ~114MB, so we do NOT ship PyAV in the plugin
zip. Instead the plugin exposes "Install PyAV" actions that fetch the matching
wheel from PyPI at runtime and unpack it under data_dir/vendor/<arch>/<version>/, where
compositor_worker.py loads it.

No pip is required (the container has none): we use the PyPI JSON API + urllib.
"""

import json
import fcntl
import hashlib
import logging
import os
import platform
import secrets
import tempfile
import urllib.request
import zipfile

logger = logging.getLogger(__name__)

PYAV_VERSION = "18.1.0"
PY_TAG = "cp313"

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


def arch_dir(arch: str, data_dir: str) -> str:
    return os.path.join(data_dir, "vendor", arch, PYAV_VERSION)


def pyav_status(arch: str, data_dir: str) -> "str | None":
    """Return the required installed PyAV version, or None if absent."""
    d = arch_dir(arch, data_dir)
    if not os.path.isdir(os.path.join(d, "av")):
        return None
    for name in os.listdir(d):
        if name.startswith("av-") and name.endswith(".dist-info"):
            return name[len("av-"):-len(".dist-info")]
    return "installed"


def _get_settings() -> dict:
    try:
        from apps.plugins.models import PluginConfig
        return PluginConfig.objects.get(key="multiview").settings
    except Exception:
        return {}


def _save_settings(updates: dict):
    from . import config
    config.update_settings(updates)


def _matches_wheel(fn: str, arch: str) -> bool:
    """Whether *fn* is a compatible manylinux PyAV wheel for this runtime."""
    info = ARCHES[arch]
    # PyAV 17+ distributes a cp311-abi3 wheel that is compatible with CPython
    # 3.13. Older releases used a cp313-specific wheel, so support both tags.
    python_compatible = f"-{PY_TAG}-" in fn or "-cp311-abi3-" in fn
    return (fn.endswith(".whl") and python_compatible and "manylinux" in fn
            and info["token"] in fn)


def _find_wheel(arch: str):
    """Return the URL, filename and SHA-256 of a compatible PyPI wheel."""
    api = f"https://pypi.org/pypi/av/{PYAV_VERSION}/json"
    with urllib.request.urlopen(api, timeout=30) as r:
        data = json.load(r)
    for f in data.get("urls", []):
        fn = f.get("filename", "")
        digest = f.get("digests", {}).get("sha256", "")
        if _matches_wheel(fn, arch) and os.path.basename(fn) == fn and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest):
            return f["url"], fn, digest
    raise RuntimeError(
        f"no {PY_TAG} manylinux {ARCHES[arch]['token']} wheel in av {PYAV_VERSION} on PyPI")


def install_pyav(arch: str, data_dir: str, *, automatic=False) -> dict:
    """Stage and publish the required PyAV wheel in durable plugin data."""
    if arch not in ARCHES:
        return {"status": "error", "message": f"Unsupported arch: {arch!r}"}
    vendor = os.path.join(data_dir, "vendor")
    dest = arch_dir(arch, data_dir)
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(os.path.join(vendor, f".{arch}.lock"), "a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                if automatic and not _get_settings().get(f"pyav_consent_{arch}"):
                    return {"status": "success", "message": "Automatic PyAV installation is not authorized"}
                if pyav_status(arch, data_dir) != PYAV_VERSION:
                    url, fn, expected_digest = _find_wheel(arch)
                    with tempfile.TemporaryDirectory(prefix=".install-", dir=vendor) as tmp:
                        whl = os.path.join(tmp, fn)
                        logger.info("multiview: downloading %s for %s", fn, arch)
                        with urllib.request.urlopen(url, timeout=30) as response, open(whl, "wb") as output:
                            digest = hashlib.sha256()
                            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                                digest.update(chunk)
                                output.write(chunk)
                        if digest.hexdigest() != expected_digest:
                            raise ValueError("Downloaded PyAV wheel failed SHA-256 verification")
                        staged = os.path.join(tmp, "staged")
                        os.makedirs(staged)
                        with zipfile.ZipFile(whl) as wheel:
                            wheel.extractall(staged)
                        if not os.path.isdir(os.path.join(staged, "av")) or not os.path.isdir(os.path.join(staged, f"av-{PYAV_VERSION}.dist-info")):
                            raise ValueError("Downloaded wheel is missing PyAV or its version metadata")
                        previous = None
                        if os.path.lexists(dest):
                            previous = os.path.join(vendor, f".{arch}-incomplete-{secrets.token_hex(16)}")
                            os.rename(dest, previous)
                        try:
                            os.rename(staged, dest)
                        except Exception:
                            if previous:
                                os.rename(previous, dest)
                            raise
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    except PermissionError as e:
        logger.error(f"multiview: PyAV install permission error: {e}")
        return {"status": "error", "message": (
            f"Install failed (permission denied writing {vendor}). Details: {e}")}
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
        return {"status": "partial", "message": f"{msg} Could not save automatic repair consent: {e}"}
    return {"status": "success", "message": msg}


def maybe_auto_install(arch: str, data_dir: str) -> "dict | None":
    """If the user has previously consented (a prior successful 'Install
    PyAV' run for this arch) and the vendored copy is now missing or
    doesn't match the pinned PYAV_VERSION (e.g. reset by a plugin update),
    reinstall it automatically. No-op if consent was never given, or the
    install is already current. Returns the install result dict, or None
    if nothing needed to be done."""
    if not arch:
        return None
    settings = _get_settings()
    if not settings.get(f"pyav_consent_{arch}"):
        return None
    if pyav_status(arch, data_dir) == PYAV_VERSION:
        return None

    result = install_pyav(arch, data_dir, automatic=True)
    if result.get("status") != "success":
        try:
            _save_settings({"pyav_auto_install_error": result.get("message", "unknown error")})
        except Exception:
            pass
    return result
