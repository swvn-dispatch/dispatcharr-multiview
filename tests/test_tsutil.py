"""Unit tests for src/tsutil.py — runnable without pytest.

    python3 tests/test_tsutil.py

Imports tsutil directly from src/ so the package __init__ (which pulls in
Dispatcharr) is not executed.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import tsutil  # noqa: E402

VIDEO_PID = 0x100
AUDIO_PID = 0x101


def make_packet(pid, *, pusi=False, afc=1, cc=0, rai=False, payload=b""):
    """Build one 188-byte TS packet.

    afc: 1=payload only, 2=AF only, 3=AF+payload.
    rai: set adaptation-field random_access_indicator (requires AF).
    """
    b = bytearray(tsutil.PACKET_SIZE)
    b[0] = tsutil.SYNC_BYTE
    b[1] = ((0x40 if pusi else 0) | ((pid >> 8) & 0x1F))
    b[2] = pid & 0xFF
    b[3] = ((afc & 0x3) << 4) | (cc & 0x0F)
    idx = 4
    if afc in (2, 3):
        # adaptation field: length byte, flags byte, stuffing
        flags = 0x40 if rai else 0x00
        af_len = 1  # just the flags byte
        b[4] = af_len
        b[5] = flags
        idx = 5 + af_len  # after flags (af_len counts the flags byte)
        # pad stuffing already zero
    if afc in (1, 3) and payload:
        end = min(tsutil.PACKET_SIZE, idx + len(payload))
        b[idx:end] = payload[: end - idx]
    return bytes(b)


def test_parsers():
    p = make_packet(VIDEO_PID, pusi=True, afc=3, cc=5, rai=True)
    assert tsutil.pid(p) == VIDEO_PID
    assert tsutil.pusi(p) is True
    assert tsutil.has_payload(p) is True
    assert tsutil.has_adaptation(p) is True
    assert tsutil.af_length(p) == 1

    a = make_packet(AUDIO_PID, afc=1, cc=3)
    assert tsutil.pid(a) == AUDIO_PID
    assert tsutil.pusi(a) is False
    assert tsutil.has_adaptation(a) is False
    assert tsutil.af_length(a) == 0


def test_random_access_detection():
    kf = make_packet(VIDEO_PID, pusi=True, afc=3, rai=True)
    assert tsutil.is_random_access(kf, VIDEO_PID) is True

    # right flags but wrong PID
    assert tsutil.is_random_access(kf, AUDIO_PID) is False
    # PUSI but no RAI
    no_rai = make_packet(VIDEO_PID, pusi=True, afc=3, rai=False)
    assert tsutil.is_random_access(no_rai, VIDEO_PID) is False
    # RAI but no PUSI
    no_pusi = make_packet(VIDEO_PID, pusi=False, afc=3, rai=True)
    assert tsutil.is_random_access(no_pusi, VIDEO_PID) is False
    # payload-only packet (no AF at all)
    plain = make_packet(VIDEO_PID, pusi=True, afc=1)
    assert tsutil.is_random_access(plain, VIDEO_PID) is False


def test_set_discontinuity():
    kf = make_packet(VIDEO_PID, pusi=True, afc=3, rai=True)
    out = tsutil.set_discontinuity(kf)
    assert out[5] & 0x80, "discontinuity_indicator should be set"
    assert out[5] & 0x40, "random_access_indicator should be preserved"

    # packet without an adaptation field is returned unchanged
    plain = make_packet(AUDIO_PID, afc=1)
    assert tsutil.set_discontinuity(plain) == plain


def test_split_packets_carries_partial():
    a = make_packet(VIDEO_PID, cc=0)
    b = make_packet(AUDIO_PID, cc=0)
    buf = a + b + b"\x47\x00\x10"  # trailing partial
    whole, leftover = tsutil.split_packets(buf)
    assert len(whole) == 2
    assert whole[0] == a and whole[1] == b
    assert leftover == b"\x47\x00\x10"


def test_continuity_rewriter_monotonic_per_pid():
    rw = tsutil.ContinuityRewriter()
    # Feed video packets with deliberately bogus CCs from "two sources".
    out_ccs = []
    for src_cc in (9, 9, 2, 7):  # nonsensical input CCs (e.g. across a splice)
        pkt = make_packet(VIDEO_PID, afc=1, cc=src_cc, payload=b"x")
        out = rw.process(pkt)
        out_ccs.append(out[3] & 0x0F)
    assert out_ccs == [0, 1, 2, 3], out_ccs

    # Audio PID has its own independent counter.
    a = rw.process(make_packet(AUDIO_PID, afc=1, cc=4, payload=b"y"))
    assert (a[3] & 0x0F) == 0


def test_continuity_rewriter_af_only_repeats():
    rw = tsutil.ContinuityRewriter()
    p1 = rw.process(make_packet(VIDEO_PID, afc=1, cc=0, payload=b"x"))
    assert (p1[3] & 0x0F) == 0
    # adaptation-only packet must NOT increment CC
    afonly = rw.process(make_packet(VIDEO_PID, afc=2, cc=0))
    assert (afonly[3] & 0x0F) == 0
    p2 = rw.process(make_packet(VIDEO_PID, afc=1, cc=0, payload=b"x"))
    assert (p2[3] & 0x0F) == 1


def test_continuity_rewriter_wraps_mod16():
    rw = tsutil.ContinuityRewriter()
    last = None
    for _ in range(20):
        out = rw.process(make_packet(VIDEO_PID, afc=1, payload=b"x"))
        last = out[3] & 0x0F
    assert last == (20 - 1) % 16


def _simulate_splice():
    """Model the pipeline splice: placeholder packets, then switch to live at a
    keyframe, all through one ContinuityRewriter, flagging the first live
    keyframe with discontinuity. Returns the output packet list."""
    rw = tsutil.ContinuityRewriter()
    out = []
    # placeholder: a few video+audio payload packets
    for i in range(3):
        out.append(rw.process(make_packet(VIDEO_PID, afc=1, cc=i, payload=b"p")))
        out.append(rw.process(make_packet(AUDIO_PID, afc=1, cc=i, payload=b"p")))
    # live source begins; first emitted packet is the keyframe -> set discontinuity
    kf = make_packet(VIDEO_PID, pusi=True, afc=3, cc=13, rai=True, payload=b"L")
    kf = tsutil.set_discontinuity(kf)
    out.append(rw.process(kf))
    for i in range(2):
        out.append(rw.process(make_packet(VIDEO_PID, afc=1, cc=i, payload=b"L")))
        out.append(rw.process(make_packet(AUDIO_PID, afc=1, cc=i, payload=b"L")))
    return out


def test_splice_end_to_end():
    out = _simulate_splice()
    # all packets keep sync
    assert all(p[0] == tsutil.SYNC_BYTE for p in out)
    # video CC across the whole spliced stream is monotonic mod 16
    vccs = [p[3] & 0x0F for p in out if tsutil.pid(p) == VIDEO_PID]
    for prev, cur in zip(vccs, vccs[1:]):
        assert cur == (prev + 1) & 0x0F, (prev, cur)
    # the keyframe boundary carries the discontinuity flag and is a random access
    kfs = [p for p in out if tsutil.is_random_access(p, VIDEO_PID)]
    assert len(kfs) == 1
    assert kfs[0][5] & 0x80, "spliced keyframe must flag discontinuity"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
