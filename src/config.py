"""Plugin configuration and field definitions for Dispatcharr Multiview."""

import json
import os

# ── Constants ────────────────────────────────────────────────────────────────

PLUGIN_DB_KEY = "multiview"

DEFAULT_SERVER_PORT = 9292
DEFAULT_SERVER_HOST = "0.0.0.0"
DEFAULT_DISPATCHARR_URL = "http://localhost:9191"


def _load_plugin_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "plugin.json")
    with open(config_path, "r") as f:
        return json.load(f)


PLUGIN_CONFIG = _load_plugin_config()

# ── Global fields (always shown) ─────────────────────────────────────────────

_GLOBAL_FIELDS = [
    {
        "id": "dispatcharr_base_url",
        "label": "Dispatcharr Base URL",
        "type": "string",
        "default": DEFAULT_DISPATCHARR_URL,
        "description": (
            "Internal base URL of your Dispatcharr instance. "
            "FFmpeg uses this to fetch the individual channel streams. "
            "Use http://localhost:PORT matching your Dispatcharr setup"
        ),
        "placeholder": "http://localhost:9191",
    },
    {
        "id": "server_host",
        "label": "Streaming Server Host",
        "type": "string",
        "default": DEFAULT_SERVER_HOST,
        "description": "Host address for the multiview streaming server (0.0.0.0 for all interfaces)",
        "placeholder": "0.0.0.0",
    },
    {
        "id": "server_port",
        "label": "Streaming Server Port",
        "type": "number",
        "default": DEFAULT_SERVER_PORT,
        "description": "Port the multiview streaming server listens on",
        "placeholder": str(DEFAULT_SERVER_PORT),
    },
]

_MULTIVIEW_COUNT_FIELD = {
    "id": "multiview_count",
    "label": "Number of Multiview Layouts",
    "type": "number",
    "default": 1,
    "min": 1,
    "description": (
        "How many multiview streams to define. "
        "After changing this value, save and refresh to see the new layout blocks"
    ),
    "placeholder": "1",
}

# ── Per-layout field builders ─────────────────────────────────────────────────

_LAYOUT_OPTIONS = [
    {"value": "auto", "label": "Auto Grid"},
    {"value": "featured", "label": "Featured (main left, others stacked right)"},
]


def _build_channel_options() -> list:
    """Return channel select options from the live DB at render time."""
    opts = [{"value": "_none", "label": "— select channel —"}]
    try:
        from apps.channels.models import Channel

        for ch in Channel.objects.order_by("channel_number").values("id", "name", "channel_number"):
            num = int(ch["channel_number"]) if ch["channel_number"] is not None else ""
            opts.append({"value": str(ch["id"]), "label": f"{num} – {ch['name']}"})
    except Exception:
        pass
    return opts


def _build_multiview_block(n: int, ch_count: int) -> list:
    """Return the list of fields for multiview layout block *n* with *ch_count* channel slots."""
    channel_options = _build_channel_options()
    num = str(n)

    fields = [
        {
            "id": f"multiview_{n}_header",
            "label": f"── Layout {n} ──────────────────────",
            "type": "info",
            "description": "",
        },
        {
            "id": f"multiview_{n}_name",
            "label": f"Layout {n} Name",
            "type": "string",
            "default": f"Multiview {n}",
            "description": "Name shown in the M3U playlist",
            "placeholder": f"Multiview {n}",
        },
        {
            "id": f"multiview_{n}_layout",
            "label": f"Layout {n} Style",
            "type": "select",
            "default": "auto",
            "options": _LAYOUT_OPTIONS,
            "description": (
                "Auto Grid: square-ish tile grid sized automatically from channel count. "
                "Featured: first channel large on the left, remaining channels stacked on the right"
            ),
        },
        {
            "id": f"multiview_{n}_channel_count",
            "label": f"Layout {n} Channel Count",
            "type": "number",
            "default": 4,
            "min": 2,
            "description": (
                f"Number of channels to tile in layout {n}. "
                "After changing, save and refresh to see the new channel slots"
            ),
            "placeholder": "4",
        },
    ]

    for m in range(1, ch_count + 1):
        audio_note = " (audio source)" if m == 1 else " (muted)"
        fields.append(
            {
                "id": f"multiview_{n}_channel_{m}",
                "label": f"Layout {n} – Channel {m}{audio_note}",
                "type": "select",
                "default": "_none",
                "options": channel_options,
                "description": (
                    "Audio from channel 1 only; all other channels are muted in the output"
                    if m == 1
                    else ""
                ),
            }
        )

    return fields


def build_plugin_fields(settings: dict) -> list:
    """Build the full field list based on current settings."""
    mv_count = max(1, int(settings.get("multiview_count", 1)))

    fields = list(_GLOBAL_FIELDS)
    fields.append(_MULTIVIEW_COUNT_FIELD)

    for n in range(1, mv_count + 1):
        ch_count = max(2, int(settings.get(f"multiview_{n}_channel_count", 4)))
        fields.extend(_build_multiview_block(n, ch_count))

    return fields


# Default field list (1 layout, 4 channels) used as plugin.json fallback
PLUGIN_FIELDS = build_plugin_fields({})
