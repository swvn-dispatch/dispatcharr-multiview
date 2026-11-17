"""Helpers for safely writing plugin-owned files."""

import os
import secrets
import shutil
import stat


def _open_temporary(directory, binary=False):
    for _ in range(10):
        temporary = os.path.join(directory, f".multiview-{secrets.token_hex(16)}.tmp")
        try:
            output = open(temporary, "xb") if binary else open(temporary, "x", encoding="utf-8")
        except FileExistsError:
            continue
        return temporary, output
    raise FileExistsError("Could not allocate a temporary Multiview file")


def atomic_write_text(path, content):
    """Replace a UTF-8 text file only after its complete contents are written."""
    directory = os.path.dirname(os.path.abspath(path))
    temporary, output = _open_temporary(directory)

    try:
        with output:
            output.write(content)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def copy_legacy_file(source, destination):
    """Copy a legacy file without replacing an existing destination.

    Return "copied", "existing", or "missing". A complete temporary file is
    linked into place atomically, so concurrent migrations never expose partial
    content or overwrite a destination created by another writer.
    """
    if os.path.lexists(destination):
        if not stat.S_ISREG(os.lstat(destination).st_mode):
            raise OSError(f"Destination is not a regular file: {destination}")
        return "existing"
    try:
        source_stat = os.lstat(source)
    except FileNotFoundError:
        return "missing"
    if not stat.S_ISREG(source_stat.st_mode):
        raise OSError(f"Legacy file is not a regular file: {source}")

    directory = os.path.dirname(os.path.abspath(destination))
    os.makedirs(directory, exist_ok=True)
    temporary, output = _open_temporary(directory, binary=True)

    try:
        with output, open(source, "rb") as legacy:
            shutil.copyfileobj(legacy, output)
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if not stat.S_ISREG(os.lstat(destination).st_mode):
                raise OSError(f"Destination is not a regular file: {destination}")
            return "existing"
        return "copied"
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def migrate_backgrounds(settings, code_dir, data_dir):
    """Copy referenced legacy backgrounds, retaining every settings reference.

    Return the names of files absent from both locations so the caller can
    report them without destroying configuration or the only surviving copy.
    """
    missing = []
    styles = settings.get("multiview_custom_layouts") or {}
    for style in styles.values():
        if not isinstance(style, dict):
            continue
        filename = style.get("background_image")
        if not filename:
            continue
        if not isinstance(filename, str) or os.path.basename(filename) != filename or filename in (".", ".."):
            raise ValueError("Invalid background filename in saved settings")
        source = os.path.join(code_dir, "style_backgrounds", filename)
        destination = os.path.join(data_dir, "style_backgrounds", filename)
        if copy_legacy_file(source, destination) == "missing":
            missing.append(filename)
    return missing


def migrate_legacy_files(settings, code_dir, data_dir):
    """Copy generated files and referenced backgrounds into durable storage.

    Callers must update database paths only after this function succeeds. Missing
    generated files can be regenerated; missing backgrounds are reported and
    their settings are left untouched.
    """
    files = {}
    for name in ("multiview.m3u", "multiview_epg.xml"):
        files[name] = copy_legacy_file(os.path.join(code_dir, name), os.path.join(data_dir, name))
    return {"files": files, "missing_backgrounds": migrate_backgrounds(settings, code_dir, data_dir)}
