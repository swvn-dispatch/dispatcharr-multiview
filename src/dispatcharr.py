"""Thin wrappers over Dispatcharr's live-proxy internals.

The live tile encoder reads a real channel over HTTP from the realsrc endpoint;
this module is what that endpoint calls to produce the channel's MPEG-TS bytes.
It mirrors the init -> add_client -> get_buffer -> StreamGenerator -> cleanup
sequence of apps/proxy/live_proxy/views.py::stream_ts so the channel shows up in
Dispatcharr's stats and is torn down cleanly when the tile goes away.

All imports are deferred into the functions: this only ever runs inside the
Dispatcharr process, but the module must still be importable standalone (tests,
command builders) without Django configured.
"""

import logging
import uuid as _uuid

logger = logging.getLogger(__name__)

USER_AGENT = "multiview-plugin"
CLIENT_IP = "127.0.0.1"
RECOVERY_BEHIND_CHUNKS = 4


class _LiveEdgeRecoveryBuffer:
    """Adapt expired multiview cursors to a recent live-buffer position.

    StreamGenerator normally retries from the oldest retained chunk. That is
    appropriate for catch-up clients, but a PTS-paced compositor can remain
    behind and expire repeatedly. A tile holding its last frame should instead
    resume a few chunks behind live so decoding can begin on a recent GOP.
    """
    def __init__(self, buffer, channel_id):
        self._buffer = buffer
        self._channel_id = channel_id

    def __getattr__(self, name):
        return getattr(self._buffer, name)

    def find_oldest_available_chunk(self, local_index):
        if self._buffer.index > local_index:
            recovery_index = max(0, self._buffer.index - RECOVERY_BEHIND_CHUNKS)
            logger.warning(
                "multiview: channel %s child cursor expired at %s; rebasing to index %s "
                "(%s chunks behind live index %s)",
                self._channel_id, local_index, recovery_index,
                RECOVERY_BEHIND_CHUNKS, self._buffer.index,
            )
            return recovery_index
        return self._buffer.find_oldest_available_chunk(local_index)


def resolve_channel_uuid(value) -> "str | None":
    """Accept a Dispatcharr channel id (int/str pk) or a channel uuid, return the
    canonical channel uuid string.

    Dispatcharr's live proxy keys channels by uuid throughout (see
    url_utils.get_stream_info_for_switch -> get_object_or_404(Channel, uuid=...)),
    so the uuid is the identifier we must pass to live_stream, not the integer pk.
    """
    # Already a uuid?
    try:
        return str(_uuid.UUID(str(value)))
    except (TypeError, ValueError):
        pass
    # Otherwise treat it as an integer pk and look up the uuid.
    try:
        from apps.channels.models import Channel
        return str(Channel.objects.values_list("uuid", flat=True).get(id=int(value)))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"multiview: cannot resolve channel uuid for {value!r}: {e}")
        return None


def live_stream(channel_id, child_buffer_seconds=None):
    """Yield MPEG-TS chunks for a real Dispatcharr channel.

    Generator. Initializes the channel if needed, registers a client, and
    streams via StreamGenerator with channel_initializing=True (so Dispatcharr's
    own connect/buffer wait applies). Always removes the client on exit, whether
    the consumer disconnects (GeneratorExit) or the stream ends.

    If the channel is already managed by live_proxy (e.g. mid-fallback or in its
    shutdown grace period), we skip initialize_channel() so we don't reset its
    stream selection back to URL 1. We just join the existing channel as a new
    client and let live_proxy continue its retry/fallback cycle.
    """
    from apps.proxy.live_proxy.server import ProxyServer
    from apps.proxy.live_proxy.services.channel_service import ChannelService
    from apps.proxy.live_proxy.url_utils import get_stream_info_for_switch
    from apps.proxy.live_proxy.output.ts.generator import StreamGenerator

    class MultiviewStreamGenerator(StreamGenerator):
        """Always start multiview inputs at Dispatcharr's live edge."""
        def _setup_streaming(self):
            if not super()._setup_streaming():
                return False
            self.buffer = _LiveEdgeRecoveryBuffer(self.buffer, self.channel_id)
            self.local_index = max(0, self.buffer.index)
            logger.info(
                f"multiview: channel {self.channel_id} starts at live "
                f"at buffer index {self.local_index}"
            )
            return True

    proxy = ProxyServer.get_instance()
    client_manager = proxy.client_managers.get(channel_id)

    if client_manager is None:
        info = get_stream_info_for_switch(channel_id)
        if not info or info.get("error"):
            err = (info or {}).get("error", "no stream info")
            logger.warning(f"multiview: channel {channel_id} unavailable: {err}")
            return

        ok = ChannelService.initialize_channel(
            channel_id,
            info["url"],
            info.get("user_agent"),
            transcode=info.get("transcode", False),
            stream_profile_value=info.get("stream_profile"),
            stream_id=info.get("stream_id"),
            m3u_profile_id=info.get("m3u_profile_id"),
            stream_name=info.get("stream_name"),
        )
        if not ok:
            logger.warning(f"multiview: initialize_channel failed for {channel_id}")
            return

        client_manager = proxy.client_managers.get(channel_id)
        if client_manager is None:
            logger.warning(f"multiview: no client manager for channel {channel_id} after init")
            return

    client_id = str(_uuid.uuid4())

    client_manager.add_client(
        client_id, USER_AGENT,
        user_agent=USER_AGENT, user=None, output_format="mpegts",
    )

    try:
        buffer = proxy.get_buffer(channel_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"multiview: get_buffer failed for {channel_id}: {e}")
        _safe_remove(client_manager, client_id)
        return

    logger.info(f"multiview: streaming channel {channel_id} as client {client_id}")
    gen = MultiviewStreamGenerator(
        channel_id, client_id, CLIENT_IP, USER_AGENT,
        channel_initializing=True, user=None, buffer=buffer,
    ).generate()

    end_reason = "source stream ended"
    try:
        for chunk in gen:
            if chunk:
                yield chunk
    except GeneratorExit:
        end_reason = "multiview tile consumer disconnected"
        raise
    except Exception as e:  # noqa: BLE001
        end_reason = f"source stream failed: {e}"
        raise
    finally:
        try:
            gen.close()
        except Exception:
            pass
        _safe_remove(client_manager, client_id)
        logger.info(f"multiview: channel {channel_id} client {client_id} closed: {end_reason}")


def _safe_remove(client_manager, client_id) -> None:
    try:
        client_manager.remove_client(client_id)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"multiview: remove_client {client_id} failed: {e}")
