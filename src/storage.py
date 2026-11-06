"""Durable plugin storage and migration from the replaceable code directory."""

import os
import shutil
import stat
import tempfile


def data_dir():
    """Resolve the host-provided storage path when no runtime context is available."""
    from apps.plugins.loader import PluginManager

    plugin = PluginManager.get().get_plugin("multiview")
    if not plugin or not plugin.data_dir:
        raise RuntimeError("Multiview durable storage is not available")
    return plugin.data_dir


def atomic_write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".multiview-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def migrate_backgrounds(settings, code_dir, durable_dir):
    """Copy available backgrounds and clear only references proven missing.

    Returns updated settings and a changed flag. I/O errors propagate so an
    unavailable mount never causes style references to be removed.
    """
    styles = settings.get("multiview_custom_layouts") or {}
    if not styles:
        return settings, False
    destination = os.path.join(durable_dir, "style_backgrounds")
    source = os.path.join(code_dir, "style_backgrounds")
    os.makedirs(destination, exist_ok=True)
    os.listdir(destination)
    try:
        os.listdir(source)
    except FileNotFoundError:
        pass
    if not os.access(destination, os.R_OK | os.W_OK | os.X_OK):
        raise PermissionError(f"Durable background directory is inaccessible: {destination}")
    for base in (destination, source):
        if os.path.exists(base) and (os.path.islink(base) or not os.path.isdir(base)):
            raise OSError(f"Invalid background directory: {base}")

    updated_styles = dict(styles)
    missing = list(settings.get("multiview_missing_backgrounds") or [])
    changed = False
    for style_id, original in styles.items():
        if not isinstance(original, dict):
            continue
        filename = original.get("background_image")
        if not filename:
            continue
        if not isinstance(filename, str) or os.path.basename(filename) != filename or filename in (".", ".."):
            continue
        target = os.path.join(destination, filename)
        legacy = os.path.join(source, filename)
        try:
            target_stat = os.lstat(target)
            if not stat.S_ISREG(target_stat.st_mode):
                raise OSError(f"Invalid background file: {target}")
            continue
        except FileNotFoundError:
            pass
        try:
            legacy_stat = os.lstat(legacy)
            if not stat.S_ISREG(legacy_stat.st_mode):
                raise OSError(f"Invalid legacy background file: {legacy}")
        except FileNotFoundError:
            style = dict(original)
            style.pop("background_image", None)
            updated_styles[style_id] = style
            label = str(original.get("name") or style_id)
            if label not in missing:
                missing.append(label)
            changed = True
            continue
        try:
            with open(legacy, "rb") as source_file, open(target, "xb") as target_file:
                try:
                    shutil.copyfileobj(source_file, target_file)
                except Exception:
                    os.unlink(target)
                    raise
        except FileExistsError:
            pass
    if not changed:
        return settings, False
    return {**settings, "multiview_custom_layouts": updated_styles,
            "multiview_missing_backgrounds": missing}, True
