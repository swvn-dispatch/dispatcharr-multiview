"""Plugin configuration and field definitions for Dispatcharr Multiview."""

import datetime
import glob
import json
import os
import re
import secrets
import shlex

try:
    from .regex_order import regex_sort_key
except ImportError:
    from regex_order import regex_sort_key  # script context (compositor_worker.py)


def _load_plugin_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "plugin.json")
    with open(config_path, "r") as f:
        return json.load(f)


PLUGIN_CONFIG = _load_plugin_config()


def load_settings():
    """Normalize persisted settings while holding the shared database row lock."""
    from django.db import transaction
    from apps.plugins.models import PluginConfig

    with transaction.atomic():
        cfg = PluginConfig.objects.select_for_update().get(key="multiview")
        settings, changed1 = ensure_layout_order(cfg.settings)
        settings, changed2 = reconcile_layout_count(settings)
        settings, changed3 = ensure_custom_layout_order(settings)
        settings, changed4 = migrate_channel_rows(settings)
        settings, changed5 = sync_channel_audio(settings)
        if changed1 or changed2 or changed3 or changed4 or changed5:
            cfg.settings = settings
            cfg.save(update_fields=["settings", "updated_at"])
        return project_channel_rows(settings)


def update_settings(updates):
    """Merge updates against the latest settings under the same row lock."""
    from django.db import transaction
    from apps.plugins.models import PluginConfig

    with transaction.atomic():
        cfg = PluginConfig.objects.select_for_update().get(key="multiview")
        settings = dict(cfg.settings or {})
        settings, _ = ensure_layout_order(settings)
        settings, _ = migrate_channel_rows(settings)
        settings, _ = sync_channel_audio(settings)
        for key, value in updates.items():
            if value is None:
                settings.pop(key, None)
            else:
                settings[key] = value
        if updates.get("multiview_order") is not None:
            settings["multiview_count"] = len(settings["multiview_order"])
        settings, _ = migrate_channel_rows(settings)
        settings = apply_dashboard_channel_updates(settings, updates)
        settings, _ = sync_channel_audio(settings, explicit_audio=set(updates))
        cfg.settings = settings
        cfg.save(update_fields=["settings", "updated_at"])

_ENCODER_OPTIONS = [
    {"value": "libx264",    "label": "Software (libx264)"},
    {"value": "h264_nvenc", "label": "NVIDIA NVENC (h264_nvenc)"},
    {"value": "h264_qsv",   "label": "Intel QSV (h264_qsv)"},
    {"value": "h264_vaapi", "label": "Intel/AMD VAAPI (h264_vaapi)"},
]


# Global fields (always shown)

_GLOBAL_SETTINGS_FIELDS = [
    {
        "id": "dash_enabled",
        "label": "Web Dashboard",
        "type": "select",
        "default": "disabled",
        "options": [
            {"value": "disabled", "label": "Disabled"},
            {"value": "enabled",  "label": "Enabled"},
        ],
        "description": (
            "Serves a mobile-friendly PWA dashboard on port 9292 (fixed -- "
            "shared with the multiview stream output itself, not "
            "configurable) for editing settings and managing active streams "
            "without the Dispatcharr admin UI. Off by default. You may need "
            "to add 9292:9292 to your docker-compose.yml ports to reach it "
            "from outside the container. Changing this setting requires a "
            "plugin/Dispatcharr reload (e.g. restart Dispatcharr) to take effect."
        ),
    },
    {
        "id": "dash_path",
        "label": "Dashboard Mount Path",
        "type": "string",
        "default": "/dash",
        "placeholder": "/dash",
        "description": (
            "URL path the dashboard is served under, e.g. '/dash' gives "
            "http://<host>:9292/dash/. Takes effect immediately, no restart "
            "needed. Avoid '/stream' and '/internal', which are reserved for "
            "the plugin's own streaming endpoints."
        ),
    },
    {
        "id": "epg_refresh_hours",
        "label": "Auto-Refresh Interval (hours)",
        "type": "number",
        "default": 24,
        "min": 0,
        "max": 168,
        "placeholder": "24",
        "description": "How often to automatically regenerate M3U and EPG. 0 = manual only (Regenerate M3U button).",
    },
    {
        "id": "live_reload_interval",
        "label": "Live Multiview Reload",
        "type": "select",
        "default": "manual",
        "options": [
            {"value": "manual", "label": "Manual"},
            {"value": "15", "label": "Every 15 seconds"},
            {"value": "30", "label": "Every 30 seconds"},
            {"value": "60", "label": "Every 60 seconds"},
            {"value": "120", "label": "Every 2 minutes"},
            {"value": "300", "label": "Every 5 minutes"},
        ],
        "description": (
            "Checks each active multiview for changed regex matches or stream-affecting "
            "layout settings, then reloads only affected streams. Reloading briefly "
            "disconnects players while Dispatcharr reconnects. Manual disables checks."
        ),
    },
    {
        "id": "audio_track_labels",
        "label": "Audio Track Labels",
        "type": "select",
        "default": "enabled",
        "options": [
            {"value": "enabled", "label": "Generated from channel names"},
            {"value": "disabled", "label": "No generated labels"},
        ],
        "description": (
            "Generates title and language metadata for multiview audio tracks. "
            "Disabling this omits generated labels; source metadata cannot be "
            "preserved because audio is decoded to raw PCM before output."
        ),
    },
]

_VIDEO_OUTPUT_FIELDS = [
    {
        "id": "output_resolution",
        "label": "Output Resolution",
        "type": "select",
        "default": "1920x1080",
        "options": [
            {"value": "3840x2160", "label": "4K (3840×2160)"},
            {"value": "1920x1080", "label": "1080p (1920×1080)"},
            {"value": "1280x720",  "label": "720p (1280×720)"},
            {"value": "854x480",   "label": "480p (854×480)"},
        ],
        "description": "Resolution of the tiled output. 4K requires substantially more CPU/GPU power and bandwidth; lower resolutions reduce both.",
    },
    {
        "id": "output_fps",
        "label": "Output Frame Rate",
        "type": "select",
        "default": "30",
        "options": [
            {"value": "24", "label": "24 fps"},
            {"value": "25", "label": "25 fps"},
            {"value": "30000/1001", "label": "29.97 fps"},
            {"value": "30", "label": "30 fps"},
            {"value": "50", "label": "50 fps"},
            {"value": "60000/1001", "label": "59.94 fps"},
            {"value": "60", "label": "60 fps"},
        ],
        "description": "Frame rate of the tiled output. Higher rates are smoother but use more CPU.",
    },
    {
        "id": "output_bitrate",
        "label": "Output Bitrate (kbps)",
        "type": "number",
        "default": 8000,
        "min": 1000,
        "max": 40000,
        "placeholder": "8000",
        "description": "Target output video bitrate in kbps (CBR). Higher values improve quality at the cost of bandwidth. 8000 is a good baseline for 1080p multiview; 12000-16000 for noticeably sharper tiles. 4K needs substantially more bitrate.",
    },
]

_VIDEO_ENCODER_FIELD = {
    "id": "video_encoder",
    "label": "Video Encoder",
    "type": "select",
    "default": "libx264",
    "options": [],  # populated from _ENCODER_OPTIONS in build_plugin_fields
    "description": "Software encoder or hardware GPU encoder. Hardware profiles automatically use GPU tile composition when this FFmpeg build supports it; otherwise streams retain CPU composition. NVENC requires NVIDIA GPU; QSV/VAAPI require Intel/AMD GPU with /dev/dri support.",
}

# Per-encoder quality / preset fields
#
# ENCODER_PRESETS maps encoder name -> (valid_preset_set, default_preset).
# server.py imports this for validation; values must stay in sync with the
# option lists in the field builders below.
ENCODER_PRESETS: dict[str, tuple[frozenset, str]] = {}


def _register_presets(encoder: str, fields_fn):
    """Populate ENCODER_PRESETS from a field builder's options list."""
    for f in fields_fn():
        if f.get("id") == "encoder_preset":
            vals = frozenset(o["value"] for o in f.get("options", []))
            ENCODER_PRESETS[encoder] = (vals, f.get("default", ""))
            return


def _render_device_field() -> dict:
    """Render-device override for QSV/VAAPI, populated from detected DRM nodes.

    Auto (the default) preserves the historic behavior of picking the first
    /dev/dri/renderD* node. In a VM or container with multiple render nodes the
    first one can be the wrong GPU, so the user can pin a specific node here.
    Options are globbed on the host at field-build time, so both the native
    Dispatcharr settings page and the web dashboard list the real nodes.
    """
    devices = sorted(glob.glob("/dev/dri/render*"))
    options = [{"value": "auto", "label": "Auto (first detected)"}]
    options += [{"value": d, "label": d} for d in devices]
    return {
        "id": "render_device",
        "label": "Render Device",
        "type": "select",
        "default": "auto",
        "options": options,
        "description": (
            "GPU render node for QSV/VAAPI. Auto picks the first detected; "
            "override if you have multiple GPUs."
        ),
    }


def _x264_fields() -> list:
    return [
        {
            "id": "encoder_preset",
            "label": "Encoder Preset",
            "type": "select", "default": "ultrafast",
            "options": [
                {"value": "ultrafast", "label": "Ultrafast (lowest CPU)"},
                {"value": "superfast", "label": "Superfast"},
                {"value": "veryfast",  "label": "Very Fast"},
                {"value": "faster",    "label": "Faster"},
                {"value": "fast",      "label": "Fast"},
                {"value": "medium",    "label": "Medium"},
                {"value": "slow",      "label": "Slow (highest quality)"},
            ],
            "description": "Speed vs quality tradeoff. Ultrafast is recommended for live tiling.",
        },
    ]


def _nvenc_fields() -> list:
    return [
        {
            "id": "encoder_preset",
            "label": "Encoder Preset",
            "type": "select", "default": "p4",
            "options": [
                {"value": "p1", "label": "p1 - Fastest (lowest quality)"},
                {"value": "p2", "label": "p2 - Fast"},
                {"value": "p3", "label": "p3 - Balanced-Fast"},
                {"value": "p4", "label": "p4 - Balanced"},
                {"value": "p5", "label": "p5 - Balanced-Quality"},
                {"value": "p6", "label": "p6 - Slow"},
                {"value": "p7", "label": "p7 - Slowest (highest quality)"},
            ],
            "description": "NVENC encode speed vs quality. p1-p2 recommended for live multiview.",
        },
    ]


def _qsv_fields() -> list:
    return [
        {
            "id": "encoder_preset",
            "label": "Encoder Preset",
            "type": "select", "default": "medium",
            "options": [
                {"value": "veryfast", "label": "Very Fast (lowest quality)"},
                {"value": "faster",   "label": "Faster"},
                {"value": "fast",     "label": "Fast"},
                {"value": "medium",   "label": "Medium (recommended)"},
                {"value": "slow",     "label": "Slow (higher quality)"},
            ],
            "description": "QSV encode speed vs quality. Medium is recommended for live multiview.",
        },
        _render_device_field(),
    ]


def _vaapi_fields() -> list:
    return [_render_device_field()]


_ENCODER_EXTRA_FIELDS = {
    "libx264":    _x264_fields,
    "h264_nvenc": _nvenc_fields,
    "h264_qsv":   _qsv_fields,
    "h264_vaapi": _vaapi_fields,
}

# Populate ENCODER_PRESETS from the field definitions above.
for _enc, _fn in _ENCODER_EXTRA_FIELDS.items():
    _register_presets(_enc, _fn)

_MULTIVIEW_COUNT_FIELD = {
    "id": "multiview_count",
    "label": "Number of Multiview Layouts",
    "type": "number",
    "default": 1,
    "min": 1,
    "description": (
        "How many multiview streams to define. "
        "After changing this value, save and reload the plugin to see the new layout blocks. "
        "Our own dashboard (/dash) manages layouts directly and hides this field -- "
        "it's only needed when adding a layout from this native settings page."
    ),
    "placeholder": "1",
}

# Per-layout field builders

_LAYOUT_OPTIONS = [
    {"value": "auto",         "label": "Auto Grid"},
    {"value": "featured",     "label": "Featured (main left, others stacked right)"},
    {"value": "top_featured", "label": "Top Featured (main top, others row bottom)"},
]


def _new_layout_id() -> str:
    return secrets.token_hex(4)


_BACKUP_KEYS = ("multiview_pre_migration_backup", "multiview_pre_reconcile_backup", "multiview_pre_channel_table_backup")


def _snapshot_before(settings: dict, backup_key: str) -> dict:
    """Stash a single most-recent copy of *settings* under backup_key before a
    destructive change (legacy migration rename, or reconcile_layout_count's
    shrink path deleting layout keys). Manual-recovery safety net only, not a
    version history -- overwrites any prior snapshot under the same key.

    Recovery (Django shell):
        from apps.plugins.models import PluginConfig
        cfg = PluginConfig.objects.get(key="multiview")
        cfg.settings = cfg.settings["multiview_pre_reconcile_backup"]["settings"]
        cfg.save()
    """
    clean = {k: v for k, v in settings.items() if k not in _BACKUP_KEYS}
    new_settings = dict(settings)
    new_settings[backup_key] = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "settings": clean,
    }
    return new_settings


def migrate_channel_rows(settings: dict) -> tuple:
    """Move legacy channel slots into one ordered table per layout."""
    missing = [layout_id for layout_id in settings.get("multiview_order", [])
               if f"multiview_{layout_id}_channels" not in settings]
    if not missing:
        return settings, False
    result = _snapshot_before(settings, "multiview_pre_channel_table_backup")
    for layout_id in missing:
        prefix = f"multiview_{layout_id}_"
        count = max(2, int(result.get(prefix + "channel_count", 4)))
        result[prefix + "channels"] = [
            {"channel": str(result.get(f"{prefix}channel_{index}") or "_none")}
            for index in range(1, count + 1)
        ]
        for key in list(result):
            if re.fullmatch(re.escape(prefix) + r"channel_\d+", key):
                result.pop(key)
    return result, True


def project_channel_rows(settings: dict) -> dict:
    """Expose classic channel slots to runtime and dashboard from table rows."""
    result = dict(settings)
    for layout_id in result.get("multiview_order", []):
        prefix = f"multiview_{layout_id}_"
        rows = result.get(prefix + "channels", [])
        if not isinstance(rows, list):
            raise ValueError(f"Invalid channel table for layout {layout_id}")
        if result.get(prefix + "selector_type", "classic") == "classic":
            result[prefix + "channel_count"] = len(rows)
        for index, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                raise ValueError(f"Invalid channel row for layout {layout_id}")
            result[f"{prefix}channel_{index}"] = str(row.get("channel") or "_none")
    return result


def sync_channel_audio(settings: dict, explicit_audio=frozenset()) -> tuple:
    """Keep a classic layout's audio attached to the same row after deletion."""
    result = dict(settings)
    changed = False
    for layout_id in result.get("multiview_order", []):
        prefix = f"multiview_{layout_id}_"
        key = prefix + "channels"
        rows = result.get(key, [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError(f"Invalid channel table for layout {layout_id}")
        updated_rows = []
        seen = set()
        for row in rows:
            row = dict(row)
            row_id = row.get("_row_id")
            if not isinstance(row_id, str) or not row_id or row_id in seen:
                row_id = secrets.token_hex(8)
                row["_row_id"] = row_id
                changed = True
            seen.add(row_id)
            updated_rows.append(row)
        if updated_rows != rows:
            result[key] = updated_rows
        audio_key = prefix + "audio_source"
        value = str(result.get(audio_key, "0"))
        if result.get(prefix + "selector_type", "classic") != "classic":
            if value not in {"all", "regex_first", "regex_lowest"}:
                result[audio_key] = "regex_first"
                changed = True
            continue
        if value in {"regex_first", "regex_lowest"}:
            value = "0"
            result[audio_key] = value
            changed = True
        anchor_key = prefix + "channel_audio_anchor"
        anchor = result.get(anchor_key)
        if not isinstance(anchor, dict):
            anchor = {}
        if audio_key not in explicit_audio and value == anchor.get("value") and anchor.get("row_id"):
            matching = next((i for i, row in enumerate(updated_rows) if row["_row_id"] == anchor["row_id"]), None)
            if matching is None:
                matching = 0
            if matching is not None and str(matching) != value:
                value = str(matching)
                result[audio_key] = value
                changed = True
        index = int(value) if value.isdigit() else -1
        row_id = updated_rows[index]["_row_id"] if 0 <= index < len(updated_rows) else None
        new_anchor = {"value": value, "row_id": row_id}
        if new_anchor != result.get(anchor_key):
            result[anchor_key] = new_anchor
            changed = True
    return result, changed


def apply_dashboard_channel_updates(settings: dict, updates: dict) -> dict:
    """Translate dashboard slot edits into the canonical channel table."""
    result = dict(settings)
    for layout_id in result.get("multiview_order", []):
        prefix = f"multiview_{layout_id}_"
        rows = [dict(row) for row in result.get(prefix + "channels", [])]
        count_key = prefix + "channel_count"
        classic = result.get(prefix + "selector_type", "classic") == "classic"
        if classic and count_key in updates and updates[count_key] is not None:
            count = int(updates[count_key])
            if not 2 <= count <= 9:
                raise ValueError("Channel count must be between 2 and 9")
            rows = rows[:count] + [{"channel": "_none"} for _ in range(max(0, count - len(rows)))]
        for key, value in updates.items():
            match = re.fullmatch(re.escape(prefix) + r"channel_(\d+)", key)
            if match:
                index = int(match.group(1)) - 1
                if not 0 <= index < 9:
                    raise ValueError("Channel index must be between 1 and 9")
                while len(rows) <= index:
                    rows.append({"channel": "_none"})
                rows[index]["channel"] = str(value or "_none")
                result.pop(key, None)
        result[prefix + "channels"] = rows
    return result


def _backfill_layout_names(settings: dict) -> tuple:
    """Fill in a missing multiview_{id}_name for any layout already in
    multiview_order. Covers layouts that migrated under an earlier build of
    this migration (before it started persisting the name explicitly) --
    without this, a layout stuck in that state would stay nameless forever,
    since ensure_layout_order's migration branch only ever runs once.
    """
    order = settings.get("multiview_order", [])
    missing = [
        (idx, layout_id) for idx, layout_id in enumerate(order)
        if f"multiview_{layout_id}_name" not in settings
    ]
    if not missing:
        return settings, False
    new_settings = dict(settings)
    for idx, layout_id in missing:
        new_settings[f"multiview_{layout_id}_name"] = f"Multiview {idx + 1}"
    return new_settings, True


def ensure_layout_order(settings: dict) -> tuple:
    """Ensure *settings* has a `multiview_order` list of stable layout ids.

    Position used to be identity (`multiview_{n}_*`, n = 1..multiview_count),
    but n is baked into the live stream URL and M3U tvg-id, so renumbering on
    delete/reorder silently swapped a viewer's stream on reconnect. This
    migrates any legacy numeric-keyed install to `multiview_{id}_*` keys plus
    an explicit `multiview_order` (a JSON list of ids) that's the only place
    position is recorded, or bootstraps a single fresh layout for a brand new
    install. Runs at most once per install -- a no-op once `multiview_order`
    exists.

    Returns (settings, changed). Callers must persist `settings` back to the
    DB when changed=True, since the migration must not silently re-run (and
    generate different ids) on every request.
    """
    if "multiview_order" in settings:
        return _backfill_layout_names(settings)

    new_settings = dict(settings)

    legacy_count = settings.get("multiview_count")
    is_legacy = legacy_count is not None or any(k.startswith("multiview_1_") for k in settings)
    if is_legacy:
        new_settings = _snapshot_before(new_settings, "multiview_pre_migration_backup")
        count = max(1, int(legacy_count or 1))
        order = []
        for n in range(1, count + 1):
            new_id = _new_layout_id()
            prefix = f"multiview_{n}_"
            for key in list(new_settings.keys()):
                if key.startswith(prefix):
                    suffix = key[len(prefix):]
                    new_settings[f"multiview_{new_id}_{suffix}"] = new_settings.pop(key)
            # The name field is only ever filled in client-side from the
            # field default -- persist it for real so it survives migration
            # instead of depending on that client-side illusion.
            name_key = f"multiview_{new_id}_name"
            if name_key not in new_settings:
                new_settings[name_key] = f"Multiview {n}"
            order.append(new_id)
        new_settings["multiview_order"] = order
        new_settings["multiview_count"] = len(order)
        return new_settings, True

    # Brand new install: bootstrap a single empty layout, same as the old
    # `max(1, int(settings.get("multiview_count", 1)))` default.
    new_settings["multiview_order"] = [_new_layout_id()]
    new_settings["multiview_count"] = 1
    return new_settings, True


def reconcile_layout_count(settings: dict) -> tuple:
    """Grow/shrink multiview_order to match multiview_count.

    multiview_count is the only layout-management control exposed on
    Dispatcharr's native plugin settings page. That page fully overwrites
    PluginConfig.settings on save (PluginManager.update_settings()) with no
    visibility into multiview_order, so a native-page edit to the count is
    reconciled here, lazily, the next time any of our own settings-read call
    sites runs (mirrors ensure_layout_order's own call pattern -- call this
    right after it, persist if either reports a change).
    """
    order = list(settings.get("multiview_order", []))
    desired = settings.get("multiview_count")
    if desired is None:
        return settings, False
    desired = max(1, int(desired))
    if desired == len(order):
        return settings, False

    new_settings = dict(settings)
    if desired > len(order):
        for _ in range(desired - len(order)):
            new_id = _new_layout_id()
            position = len(order) + 1
            new_settings[f"multiview_{new_id}_name"] = f"Multiview {position}"
            new_settings[f"multiview_{new_id}_layout"] = "auto"
            new_settings[f"multiview_{new_id}_selector_type"] = "classic"
            new_settings[f"multiview_{new_id}_channel_count"] = 4
            new_settings[f"multiview_{new_id}_epg_source_mode"] = "dummy"
            order.append(new_id)
    else:
        new_settings = _snapshot_before(new_settings, "multiview_pre_reconcile_backup")
        while len(order) > desired:
            removed_id = order.pop()
            prefix = f"multiview_{removed_id}_"
            for key in list(new_settings.keys()):
                if key.startswith(prefix):
                    new_settings.pop(key, None)

    new_settings["multiview_order"] = order
    new_settings["multiview_count"] = len(order)
    return new_settings, True


def ensure_custom_layout_order(settings: dict) -> tuple:
    """Ensure *settings* has a `multiview_custom_layouts_order` list of
    style ids, mirroring `ensure_layout_order`'s rationale: dict key order
    on `multiview_custom_layouts` isn't a reliable place to record display
    order (not guaranteed to round-trip through JSON storage), so an
    explicit ordered id list is kept alongside it instead. Migrates once
    from the dict's existing keys if the order list is missing, and appends
    any "orphan" ids present in the dict but absent from the order list
    (e.g. a style added directly via API without going through this path).

    Returns (settings, changed). Callers must persist `settings` back to
    the DB when changed=True.
    """
    layouts = settings.get("multiview_custom_layouts", {})
    order = settings.get("multiview_custom_layouts_order")
    if order is None:
        new_settings = dict(settings)
        new_settings["multiview_custom_layouts_order"] = list(layouts.keys())
        return new_settings, True

    orphans = [style_id for style_id in layouts.keys() if style_id not in order]
    if not orphans:
        return settings, False

    new_settings = dict(settings)
    new_settings["multiview_custom_layouts_order"] = [*order, *orphans]
    return new_settings, True


def _get_multiview_profile_params() -> str:
    """Return the ffmpeg parameters string for the globally-enabled default stream profile."""
    try:
        from core.models import CoreSettings, StreamProfile
        default_id = CoreSettings.get_default_stream_profile_id()
        profile = StreamProfile.objects.filter(id=default_id).first()
        return profile.parameters if profile else ""
    except Exception:
        return ""


def _audio_copy_may_drop_tracks(params: str) -> bool:
    """Identify copy profiles without a clearly complete input-zero audio map."""
    try:
        tokens = shlex.split(params)
    except ValueError:
        # A malformed command cannot establish a safe mapping.
        return bool(re.search(r'(?:^|\s)-(?:c|codec|c:a|codec:a|acodec)\s+copy(?:\s|$)', params))

    copy_options = {"-c", "-codec", "-c:a", "-codec:a", "-acodec"}
    if not any(option in copy_options and value == "copy"
               for option, value in zip(tokens, tokens[1:])):
        return False

    maps = [value for option, value in zip(tokens, tokens[1:]) if option == "-map"]
    complete_map = any(value in {"0", "0?", "0:a", "0:a?"} for value in maps)
    return not complete_map or "-an" in tokens or any(value.startswith("-") for value in maps)


def _build_warnings_fields(settings: dict, data_dir=None) -> list:
    """Return warning info fields for the settings page. Empty list = no warnings = section hidden."""
    warnings = []

    try:
        from . import deps as _deps
        import platform as _platform
        arch = _deps.detect_arch()
        if not arch:
            warnings.append({
                "id": "_warn_pyav_arch", "label": "Media Engine (PyAV)", "type": "info",
                "description": (f"Unsupported CPU architecture ({_platform.machine()}); "
                                f"PyAV is unavailable, streaming will not work."),
            })
        elif data_dir is not None and not _deps.pyav_status(arch, data_dir):
            if settings.get(f"pyav_consent_{arch}"):
                desc = (
                    f"PyAV is not currently installed for {arch}. Since you've "
                    f"previously installed it, the plugin will automatically "
                    f"reinstall it in the background on next load."
                )
                err = settings.get("pyav_auto_install_error")
                if err:
                    desc += (f" Last automatic attempt failed: {err}. You can also "
                             f"click 'Install PyAV' below to retry immediately.")
            else:
                desc = (f"PyAV is NOT installed for {arch}. Run the "
                        f"'Install PyAV' action below before streaming.")
            warnings.append({
                "id": "_warn_pyav_missing", "label": "Media Engine (PyAV)", "type": "info",
                "description": desc,
            })
    except Exception as e:
        warnings.append({
            "id": "_warn_pyav_unknown", "label": "Media Engine (PyAV)", "type": "info",
            "description": f"PyAV status unknown: {e}",
        })

    params = _get_multiview_profile_params()
    if params and _audio_copy_may_drop_tracks(params):
        warnings.append({
            "id": "_warn_audio_copy",
            "label": "Audio: multi-track may be dropped",
            "type": "info",
            "description": (
                "The default stream profile uses audio copy but does not clearly map "
                "all audio tracks. Players may not see every multiview audio track. "
                "Use '-map 0' in the default stream profile, or combine '-map 0:a' "
                "with an appropriate video map such as '-map 0:v'."
            ),
        })

    encoder = settings.get("video_encoder", "libx264")
    if encoder == "libx264":
        order = settings.get("multiview_order", [])
        heavy_layouts = [
            idx + 1 for idx, layout_id in enumerate(order)
            if max(2, int(settings.get(f"multiview_{layout_id}_channel_count", 4))) > 3
        ]
        if heavy_layouts:
            layout_str = ", ".join(f"Layout {n}" for n in heavy_layouts)
            warnings.append({
                "id": "_warn_sw_encode",
                "label": "Performance: software encoding with 4+ streams",
                "type": "info",
                "description": (
                    f"{layout_str} has more than 3 streams configured with software "
                    f"encoding (libx264). This is CPU-intensive and may cause dropped "
                    f"frames or slow-motion output. Enable a hardware encoder "
                    f"(NVENC, QSV, VAAPI) in Video Settings if available."
                ),
            })

    try:
        from apps.proxy.config import BaseConfig as _ProxyConfig
        proxy_settings = _ProxyConfig.get_proxy_settings()
        grace = int(proxy_settings.get("channel_init_grace_period", 5))
        if grace < 8:
            warnings.append({
                "id": "_warn_channel_init_timeout",
                "label": "Proxy: Channel Initialization Timeout is too low",
                "type": "info",
                "description": (
                    f"Channel Initialization Timeout is set to {grace}s in Dispatcharr's "
                    f"Proxy Settings. Values under 8s can cause multiview tiles to fail on "
                    f"startup before channels finish initializing. "
                    f"Set it to at least 10s in Settings → Proxy (higher on lower-power systems)."
                ),
            })
    except Exception:
        pass

    if not warnings:
        return []

    return [{
        "id": "_warnings_header",
        "label": "── Warnings ──────────────────────────",
        "type": "info",
        "description": "Use the refresh button (top-right) or restart Dispatcharr to re-check warnings.",
    }] + warnings


def _get_multiview_channel_ids() -> set:
    """Return the set of Channel IDs backed by the Dispatcharr Multiview M3U account."""
    try:
        from apps.m3u.models import M3UAccount
        from apps.channels.models import Channel
        acct = M3UAccount.objects.filter(name="Dispatcharr Multiview").first()
        if not acct:
            return set()
        return set(
            Channel.objects.filter(streams__m3u_account=acct)
            .values_list("id", flat=True)
            .distinct()
        )
    except Exception:
        return set()


def _get_streamless_channel_ids() -> set:
    """Return the set of Channel IDs with no streams assigned."""
    try:
        from django.db.models import Count
        from apps.channels.models import Channel
        return set(
            Channel.objects.annotate(n_streams=Count("streams"))
            .filter(n_streams=0)
            .values_list("id", flat=True)
        )
    except Exception:
        return set()


def _build_channel_options() -> list:
    """Return channel select options from the live DB, excluding multiview output channels."""
    excluded = _get_multiview_channel_ids() | _get_streamless_channel_ids()
    opts = [{"value": "_none", "label": "Select a channel"}]
    try:
        from apps.channels.models import Channel
        for ch in Channel.objects.order_by("channel_number").values("id", "name", "channel_number").distinct():
            if ch["id"] in excluded:
                continue
            num = int(ch["channel_number"]) if ch["channel_number"] is not None else ""
            opts.append({"value": str(ch["id"]), "label": f"{num} - {ch['name']}"})
    except Exception:
        pass
    return opts


def _build_layout_channel_options(layout_id: str, settings: dict, ch_count: int, selector_type: str, regex_pattern: str) -> list:
    """Return channel options scoped to the channels actually in this layout."""
    opts = [{"value": "_none", "label": "Select a channel"}]
    seen = set()
    try:
        from apps.channels.models import Channel
        if selector_type == "regex" and regex_pattern:
            excluded = _get_multiview_channel_ids() | _get_streamless_channel_ids()
            regex_sort = settings.get(f"multiview_{layout_id}_regex_sort", "channel_number")
            matched = list(
                Channel.objects.filter(name__iregex=regex_pattern)
                .exclude(id__in=excluded)
            )
            matched.sort(key=lambda ch: regex_sort_key(
                regex_pattern, ch.name, ch.channel_number, regex_sort,
            ))
            for ch in matched[:ch_count]:
                if ch.id in seen:
                    continue
                seen.add(ch.id)
                num = int(ch.channel_number) if ch.channel_number is not None else ""
                opts.append({"value": str(ch.id), "label": f"{num} - {ch.name}"})
        else:
            for m in range(1, ch_count + 1):
                ch_id = settings.get(f"multiview_{layout_id}_channel_{m}", "_none")
                if ch_id and ch_id != "_none" and ch_id not in seen:
                    try:
                        ch = Channel.objects.values("id", "name", "channel_number").get(id=int(ch_id))
                        seen.add(ch_id)
                        num = int(ch["channel_number"]) if ch["channel_number"] is not None else ""
                        opts.append({"value": str(ch["id"]), "label": f"{num} - {ch['name']}"})
                    except Channel.DoesNotExist:
                        pass
    except Exception:
        pass
    return opts


def _build_multiview_block(layout_id: str, position: int, ch_count: int, selector_type: str = "classic", regex_pattern: str = "", epg_source_mode: str = "dummy", layout_channel_options: list = None, layout_style_options: list = None, dashboard: bool = False, channel_rows=None) -> list:
    """Return the list of fields for multiview layout *layout_id* with *ch_count* channel slots.

    *position* is only for human-readable labels ("Layout 2 Name") -- the
    stable *layout_id* is what's baked into field ids/settings keys, so
    reordering/deleting other layouts never renames this one's keys.
    """
    is_regex = selector_type == "regex"
    n = position  # short alias for the label text below

    fields = [
        {
            "id": f"multiview_{layout_id}_header",
            "label": f"── Layout {n} ──────────────────────",
            "type": "info",
            "description": "",
        },
        {
            "id": f"multiview_{layout_id}_name",
            "label": f"Layout {n} Name",
            "type": "string",
            "default": f"Multiview {n}",
            "description": "Name shown in the M3U playlist",
            "placeholder": f"Multiview {n}",
        },
        {
            "id": f"multiview_{layout_id}_layout",
            "label": f"Layout {n} Style",
            "type": "select",
            "default": "auto",
            "options": layout_style_options or _LAYOUT_OPTIONS,
            "description": (
                "Auto Grid: square-ish tile grid sized automatically from channel count. "
                "Featured: first channel large on the left, remaining channels stacked on the right"
            ),
        },
        {
            "id": f"multiview_{layout_id}_selector_type",
            "label": f"Layout {n} Channel Selection",
            "type": "select",
            "default": "classic",
            "options": [
                {"value": "classic", "label": "Classic (dropdown)"},
                {"value": "regex",   "label": "Regex (dynamic match)"},
            ],
            "description": (
                "Classic: select channels from dropdowns. "
                "Regex: channels matching a pattern are selected automatically at stream time. "
                "After changing, save and refresh to see the relevant fields."
            ),
        },
    ]

    if is_regex or dashboard:
        fields.append({
            "id": f"multiview_{layout_id}_channel_count",
            "label": f"Layout {n} Max Channels" if is_regex else f"Layout {n} Channel Count",
            "type": "number",
            "default": 4,
            "min": 2,
            "max": 9,
            "description": (
                f"Maximum number of matching channels to tile in layout {n}. "
                "Recommended maximum is 4; higher counts may not start correctly."
            ) if is_regex else (
                f"Number of channels to tile in layout {n}. "
                "Recommended maximum is 4; higher counts may not start correctly. "
                "After changing, save and refresh to see the new channel slots."
            ),
            "placeholder": "4",
        })

    if is_regex:
        fields.append(
            {
                "id": f"multiview_{layout_id}_regex_sort",
                "label": f"Layout {n} Regex Sort",
                "type": "select",
                "default": "channel_number",
                "options": [
                    {"value": "channel_number", "label": "Channel number"},
                    {"value": "channel_number_reverse", "label": "Channel number (descending)"},
                    {"value": "pattern", "label": "Top-level regex alternatives"},
                ],
                "description": (
                    "Top-level regex alternatives uses | alternatives from left to right. "
                    "For example, event-a|event-b|event-c puts matching channels in that order."
                ),
            }
        )
        fields.append(
            {
                "id": f"multiview_{layout_id}_regex_pattern",
                "label": f"Layout {n} Channel Pattern",
                "type": "text",
                "default": "",
                "placeholder": r"e.g. ^Event \| (Alpha|Beta)$",
                "description": (
                    "Case-insensitive regex matched against channel names. "
                    "The field wraps long patterns for easier editing."
                ),
            }
        )
        audio_opts = [
            {"value": "all",         "label": "All channels (selectable in player)"},
            {"value": "regex_first", "label": "First matched channel"},
            {"value": "regex_lowest","label": "Lowest channel number"},
        ]
        audio_default = "regex_first"
    else:
        channel_options = list(_build_channel_options())
        known = {str(option["value"]) for option in channel_options}
        for row in channel_rows or []:
            value = str(row.get("channel") or "_none")
            if value not in known:
                channel_options.append({"value": value, "label": f"Unavailable channel ({value})"})
                known.add(value)
        if dashboard:
            for m in range(1, ch_count + 1):
                fields.append(
                    {
                        "id": f"multiview_{layout_id}_channel_{m}",
                        "label": f"Layout {n}: Channel {m}",
                        "type": "select",
                        "default": "_none",
                        "options": channel_options,
                        "description": "",
                    }
                )
        else:
            fields.append({
                "id": f"multiview_{layout_id}_channels",
                "label": f"Layout {n} Channels",
                "type": "table",
                "default": [{"channel": "_none"} for _ in range(4)],
                "description": "Add 2 to 9 channels in tile order. The first channel is featured in featured layouts.",
                "columns": [{"id": "channel", "label": "Channel", "type": "select", "options": channel_options}],
            })
        audio_opts = [{"value": "all", "label": "All channels (selectable in player)"}]
        for m in range(1, ch_count + 1):
            audio_opts.append({"value": str(m - 1), "label": f"Channel {m}"})
        audio_default = "0"

    fields.append(
        {
            "id": f"multiview_{layout_id}_audio_source",
            "label": f"Layout {n} Audio Source",
            "type": "select",
            "default": audio_default,
            "options": audio_opts,
            "description": (
                "Which channel's audio to include. "
                "'All channels' outputs one audio track per tile; "
                "players that support multi-track (VLC, Infuse, etc.) can switch between them."
            ),
        }
    )

    fields.append(
        {
            "id": f"multiview_{layout_id}_epg_source_mode",
            "label": f"Layout {n} EPG Source",
            "type": "select",
            "default": "dummy",
            "options": [
                {"value": "dummy",   "label": "Placeholder (built-in)"},
                {"value": "forward", "label": "Forward from channel"},
            ],
            "description": (
                "Placeholder emits a simple built-in programme entry. "
                "Forward copies real EPG data from a source channel onto this layout. "
                "After changing, save and refresh to see the relevant fields."
            ),
        }
    )

    if epg_source_mode == "forward":
        fields.append(
            {
                "id": f"multiview_{layout_id}_epg_forward_channel",
                "label": f"Layout {n} EPG Source Channel",
                "type": "select",
                "default": "_none",
                "options": layout_channel_options or _build_channel_options(),
                "description": (
                    "Channel whose EPG will be displayed for this layout. "
                    "Falls back to a placeholder entry if the channel has no EPG data."
                ),
            }
        )
    else:
        fields += [
            {
                "id": f"multiview_{layout_id}_epg_title",
                "label": f"Layout {n} EPG Title",
                "type": "string",
                "default": "",
                "placeholder": f"Multiview {n}",
                "description": "Program title shown in the EPG. Leave blank to use the layout name.",
            },
            {
                "id": f"multiview_{layout_id}_epg_subtitle",
                "label": f"Layout {n} EPG Subtitle",
                "type": "string",
                "default": "",
                "placeholder": "",
                "description": "Optional subtitle shown below the title in the EPG.",
            },
            {
                "id": f"multiview_{layout_id}_epg_categories",
                "label": f"Layout {n} EPG Categories",
                "type": "string",
                "default": "",
                "placeholder": "Sports, News",
                "description": (
                    "Comma-separated category tags. "
                    "EPG apps use these for colour coding (e.g. 'Sports' turns entries green in most players)."
                ),
            },
        ]

    return fields


_GLOBAL_SETTINGS_HEADER = {
    "id": "_global_settings_header",
    "label": "── Global Settings ──────────────────────",
    "type": "info",
    "description": "",
}

_VIDEO_SETTINGS_HEADER = {
    "id": "_video_settings_header",
    "label": "── Video Settings ───────────────────────",
    "type": "info",
    "description": "",
}


def build_plugin_fields(settings: dict, data_dir=None, *, dashboard=False) -> list:
    """Build the full field list based on current settings.

    Callers must run `ensure_layout_order()` on *settings* (and persist it if
    changed) before calling this -- this function just renders whatever
    `multiview_order` is already there.
    """
    order = settings.get("multiview_order", [])
    encoder  = settings.get("video_encoder", "libx264")

    custom_layouts = settings.get("multiview_custom_layouts", {})
    layout_style_options = _LAYOUT_OPTIONS + [
        {"value": f"custom:{style_id}", "label": f"Custom: {info.get('name') or style_id}"}
        for style_id, info in custom_layouts.items()
        if info.get("elements")
    ]

    enc_field = dict(_VIDEO_ENCODER_FIELD)
    enc_field["options"] = _ENCODER_OPTIONS

    count_field = dict(_MULTIVIEW_COUNT_FIELD)
    count_field["default"] = len(order)

    fields = _build_warnings_fields(settings, data_dir)
    fields.append(_GLOBAL_SETTINGS_HEADER)
    fields.extend(_GLOBAL_SETTINGS_FIELDS)
    fields.append(count_field)
    fields.append(_VIDEO_SETTINGS_HEADER)
    fields.extend(_VIDEO_OUTPUT_FIELDS)
    fields.append(enc_field)

    extra_fn = _ENCODER_EXTRA_FIELDS.get(encoder, _x264_fields)
    fields.extend(extra_fn())

    for idx, layout_id in enumerate(order):
        position = idx + 1
        ch_count = max(2, int(settings.get(f"multiview_{layout_id}_channel_count", 4)))
        selector_type = settings.get(f"multiview_{layout_id}_selector_type", "classic")
        regex_pattern = settings.get(f"multiview_{layout_id}_regex_pattern", "")
        epg_source_mode = settings.get(f"multiview_{layout_id}_epg_source_mode", "dummy")
        layout_ch_opts = _build_layout_channel_options(layout_id, settings, ch_count, selector_type, regex_pattern)
        fields.extend(_build_multiview_block(layout_id, position, ch_count, selector_type, regex_pattern, epg_source_mode, layout_ch_opts, layout_style_options, dashboard=dashboard, channel_rows=settings.get(f"multiview_{layout_id}_channels", [])))

    if dashboard:
        return fields

    # Section membership is explicit in Dispatcharr; visual order alone does not
    # group fields. Keep the dashboard's existing projection unchanged.
    section = None
    grouped = []
    for field in fields:
        field = dict(field)
        field_id = field.get("id", "")
        if field_id == "_warnings_header":
            section = field_id
            field.update(label="Alerts", type="section", collapsed=False)
            field.pop("description", None)
        elif field_id in {"_global_settings_header", "_video_settings_header"}:
            section = field_id
            field.update(label="Global Settings" if field_id == "_global_settings_header" else "Video Settings", type="section", collapsed=False)
            field.pop("description", None)
        elif field_id.startswith("multiview_") and field_id.endswith("_header"):
            section = "_" + field_id
            field.update(id=section, label=field["label"].strip("─ "), type="section", collapsed=True)
            field.pop("description", None)
        elif section:
            field["section"] = section
        grouped.append(field)
    return grouped


# Default field list (1 layout, 4 channels) used as plugin.json fallback
PLUGIN_FIELDS = build_plugin_fields({})
