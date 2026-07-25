"""Tests for src/cync_lan/packet/xlink_legacy.py's HDLC/PPP frame decoder.

Non-HA-dependent (imports `cync_lan.packet` directly), living alongside the
rest of the suite so the same `pytest tests/components/cync_lan/` invocation
picks it up - same pattern as test_devices.py/test_cloud_api.py.
"""

from __future__ import annotations

import struct

from cync_lan.packet.xlink_legacy import Direction, decode_xlink_frame


def _build_frame(msg_id: int, direction: int, op_code: int, payload: bytes) -> bytes:
    """Reference encoder used only by these tests - cync-lan itself never
    emits a genuine Xlink/Frame frame (see xlink_legacy.py's module
    docstring), so this exists purely to construct realistic input for the
    decoder under test."""
    body = struct.pack("<BH", op_code, len(payload)) + payload
    checksum = sum(body) % 256
    inner = (
        struct.pack("<I", msg_id)
        + struct.pack("B", direction)
        + body
        + struct.pack("B", checksum)
    )
    stuffed = bytearray()
    for b in inner:
        if b in (0x7E, 0x7D):
            stuffed.append(0x7D)
            stuffed.append(b ^ 0x20)
        else:
            stuffed.append(b)
    return bytes([0x7E]) + bytes(stuffed) + bytes([0x7E])


def test_decode_simple_frame():
    payload = struct.pack(">B", 0) + struct.pack("<H", 5)  # errorCode=0, id=5
    frame_bytes = _build_frame(12345, Direction.RSP, 0x10, payload)

    frame = decode_xlink_frame(frame_bytes)

    assert frame is not None
    assert frame.msg_id == 12345
    assert frame.direction == Direction.RSP
    assert frame.op_code == 0x10
    assert frame.payload == payload


def test_decode_handles_byte_stuffed_payload():
    """Payload containing literal 0x7E/0x7D bytes must round-trip through
    the escape/unescape scheme correctly."""
    payload = bytes([0x7E, 0x7D, 0x01, 0x02, 0x7E])
    frame_bytes = _build_frame(99, Direction.ANNOUNCE, 0x92, payload)

    frame = decode_xlink_frame(frame_bytes)

    assert frame is not None
    assert frame.payload == payload
    assert frame.msg_id == 99
    assert frame.op_code == 0x92


def test_decode_rejects_bad_checksum():
    body = struct.pack("<BH", 0x10, 3) + bytes([0, 5, 0])
    inner = (
        struct.pack("<I", 12345)
        + struct.pack("B", Direction.RSP)
        + body
        + struct.pack(
            "B",
            0xFF,  # deliberately wrong checksum
        )
    )
    frame_bytes = bytes([0x7E]) + inner + bytes([0x7E])

    assert decode_xlink_frame(frame_bytes) is None


def test_decode_rejects_invalid_direction_byte():
    body = struct.pack("<BH", 0x10, 0)
    inner = (
        struct.pack("<I", 1)
        + struct.pack("B", 0x00)
        + body
        + struct.pack("B", sum(body) % 256)
    )
    frame_bytes = bytes([0x7E]) + inner + bytes([0x7E])

    assert decode_xlink_frame(frame_bytes) is None


def test_decode_returns_none_for_non_frame_input():
    assert decode_xlink_frame(b"") is None
    assert decode_xlink_frame(b"not a frame at all") is None
    assert decode_xlink_frame(bytes([0x7E, 0x01, 0x02])) is None  # no closing delimiter
    assert decode_xlink_frame(bytes([0x7E, 0x7E])) is None  # empty inner, too short
    assert (
        decode_xlink_frame(bytes([0x01, 0x02, 0x03])) is None
    )  # doesn't even start with 0x7E


def test_decode_never_raises_on_truncated_length_field():
    """A payload claiming a length longer than what's actually present must
    fail gracefully, not raise/crash - this is called speculatively against
    arbitrary unrecognized bytes."""
    body = struct.pack("<BH", 0x10, 200) + bytes(
        [1, 2, 3]
    )  # claims 200-byte payload, has 3
    inner = struct.pack("<I", 1) + struct.pack("B", Direction.RSP) + body
    frame_bytes = bytes([0x7E]) + inner + bytes([0x7E])

    assert decode_xlink_frame(frame_bytes) is None
