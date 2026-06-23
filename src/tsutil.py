"""MPEG-TS packet helpers for seamless tile splicing.

The multiview composition reads each tile as one continuous MPEG-TS stream that
internally switches sources (logo placeholder -> live channel -> offline card).
Because every source is encoded to identical parameters (same codec, resolution,
fps, and PIDs), we can splice between them at a video keyframe and keep the
composition's decoder happy by:

  1. rewriting the continuity counter per PID so it stays monotonic across the
     switch (no spurious "continuity counter" warnings / dropped frames), and
  2. flagging the first post-splice keyframe packet with the adaptation-field
     discontinuity_indicator so the demuxer resets its timeline.

The composition also timestamps tile inputs with -use_wallclock_as_timestamps,
so internal PCR/PTS jumps at the splice are ignored; the keyframe + continuous
CC are what make the transition clean.

Everything here is pure byte manipulation: no ffmpeg, no decoding. That keeps it
cheap (header scanning only) and unit-testable in isolation.
"""

PACKET_SIZE = 188
SYNC_BYTE = 0x47
NULL_PID = 0x1FFF


def iter_packets(buf: bytes):
    """Yield whole 188-byte TS packets from buf, ignoring a trailing partial.

    Caller is responsible for carrying any partial tail over to the next call
    (see split_packets). Packets must be sync-aligned at offset 0.
    """
    n = len(buf) - (len(buf) % PACKET_SIZE)
    for off in range(0, n, PACKET_SIZE):
        yield buf[off:off + PACKET_SIZE]


def split_packets(buf: bytes):
    """Return (list_of_whole_packets, leftover_bytes).

    Use when reading from a pipe in arbitrary chunk sizes: feed leftover back in
    front of the next read so packet boundaries are preserved.
    """
    n = len(buf) - (len(buf) % PACKET_SIZE)
    whole = [buf[off:off + PACKET_SIZE] for off in range(0, n, PACKET_SIZE)]
    return whole, buf[n:]


def pid(pkt) -> int:
    return ((pkt[1] & 0x1F) << 8) | pkt[2]


def pusi(pkt) -> bool:
    """payload_unit_start_indicator."""
    return bool(pkt[1] & 0x40)


def _afc(pkt) -> int:
    """adaptation_field_control: 1=payload only, 2=AF only, 3=AF+payload."""
    return (pkt[3] >> 4) & 0x3


def has_payload(pkt) -> bool:
    return _afc(pkt) in (1, 3)


def has_adaptation(pkt) -> bool:
    return _afc(pkt) in (2, 3)


def af_length(pkt) -> int:
    """Length of the adaptation field, or 0 if none."""
    return pkt[4] if has_adaptation(pkt) else 0


def is_random_access(pkt, video_pid: int) -> bool:
    """True if pkt is a random-access point (keyframe boundary) for video_pid.

    Requires the video PID, PUSI set (start of a PES), and the adaptation-field
    random_access_indicator. ffmpeg's mpegts muxer sets RAI on the first packet
    of an IDR access unit, which is exactly where we want to splice.
    """
    if pid(pkt) != video_pid or not pusi(pkt):
        return False
    if not has_adaptation(pkt) or pkt[4] == 0:
        return False
    return bool(pkt[5] & 0x40)


def set_discontinuity(pkt) -> bytes:
    """Return pkt with the adaptation-field discontinuity_indicator set.

    Only works when the packet already carries an adaptation field with length
    >= 1 (the post-splice keyframe always does, since it has RAI/PCR). Packets
    without room for the flag are returned unchanged: TS packets are a fixed 188
    bytes, so adding an AF would require discarding payload, which we never do.
    """
    if not has_adaptation(pkt) or pkt[4] == 0:
        return bytes(pkt)
    out = bytearray(pkt)
    out[5] |= 0x80
    return bytes(out)


class ContinuityRewriter:
    """Rewrites per-PID continuity counters so output stays monotonic.

    The CC increments by one (mod 16) only for packets that carry a payload;
    adaptation-only packets repeat the last value. Feeding packets from multiple
    spliced sources through one rewriter yields a single seamless CC sequence per
    PID, so the downstream demuxer never sees a discontinuity it didn't expect.
    """

    def __init__(self):
        self._cc: dict[int, int] = {}

    def process(self, pkt) -> bytes:
        p = pid(pkt)
        if p == NULL_PID:
            return bytes(pkt)
        out = bytearray(pkt)
        if has_payload(pkt):
            nxt = (self._cc.get(p, 15) + 1) & 0x0F
        else:
            # adaptation-only: CC must repeat the previous value for this PID
            nxt = self._cc.get(p, 0)
        self._cc[p] = nxt
        out[3] = (out[3] & 0xF0) | nxt
        return bytes(out)
