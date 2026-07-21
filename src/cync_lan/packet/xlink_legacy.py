"""Decoder for the phone app's legacy Xlink/Frame HDLC-style notification
channel (XlinkTranslatorKt.m14449a()/Xlink.m14391a()/Frame.m14440a() in the
decompiled app, and XlinkNotificationParser.m14437a() on the receive side) -
used by the pure "Hub" Scenes/Schedules commands
(CreateSceneHubCommand/CreateScheduleHubCommand/DeleteSceneHubCommand/
DeleteScheduleHubCommand/ToggleAutomationHubCommand) to report results back
(e.g. a hub-allocated scene_id/schedule_id) asynchronously after the request.

Structurally distinct from cync-lan's own confirmed PacketBuilder wire
format - no delimiters/escaping there, a different msgId width, no embedded
length field - and, critically, whether this notification channel rides the
same TCP relay cync-lan intercepts at all is UNCONFIRMED (same open question
already flagged for the outgoing side of this command family - see
docs/cync_automations.md). This module only provides best-effort DECODING;
cync-lan's own outgoing sends for this command family go through its usual
confirmed envelope (broadcast_control_command), not this frame format, per
the established precedent for delete_scene/delete_schedule/toggle_automation.

Only a decoder is provided (no encoder) - cync-lan has no reason to emit a
genuine Xlink/Frame HDLC frame itself.
"""

from __future__ import annotations

import struct
from typing import NamedTuple, Optional

__all__ = ["Direction", "XlinkFrame", "decode_xlink_frame"]

DELIMITER = 0x7E
ESCAPE = 0x7D
ESCAPE_XOR = 0x20  # stuffed as ESCAPE, (original_byte ^ ESCAPE_XOR)


class Direction:
    """Frame.Direction's real byte values (Frame.java) - Xlink.m14391a()
    hardcodes the same REQ value; Frame generalizes it into this enum but
    produces byte-identical output for the REQ case."""

    REQ = 0xF8
    RSP = 0xF9
    ANNOUNCE = 0xFA

    _VALID = (REQ, RSP, ANNOUNCE)


class XlinkFrame(NamedTuple):
    msg_id: int
    direction: int
    op_code: int
    payload: bytes


def _byte_unstuff(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        if b == ESCAPE and i + 1 < n:
            out.append(data[i + 1] ^ ESCAPE_XOR)
            i += 2
        else:
            out.append(b)
            i += 1
    return bytes(out)


def decode_xlink_frame(data: bytes) -> Optional[XlinkFrame]:
    """Best-effort decode of a single 0x7E-delimited Xlink/Frame-style frame.

    `data` is the raw bytes starting with the opening 0x7E (as extracted by
    cync-lan's existing 0x7E-bound-inner-data convention, e.g.
    devices.py's `_handle_83_packet`/`_handle_73_mesh_control`). The closing
    delimiter is located here (not by the caller) because a naive
    `.find(0x7E)` would misfire on a literal 0x7E that only appears inside
    the byte-stuffed body as the two-byte escape sequence `7D 5E`.

    Layout after unstuffing (confirmed via decompiled source - Frame.java/
    Xlink.java): msgId(4B LE) + direction(1B) + op_code(1B) + len(2B LE) +
    payload(len bytes) + checksum(1B, sum-mod-256 over
    op_code+len+payload only - NOT msgId/direction).

    Returns None (never raises) if the bytes don't decode as a well-formed
    frame - this is called speculatively against inner payloads that may not
    be an Xlink frame at all (see devices.py's 0x83/0x73 "unknown ctrl_bytes"
    fallback, which falls through to its existing unknown-packet logging
    when this returns None).
    """
    if not data or data[0] != DELIMITER:
        return None

    i = 1
    end = None
    while i < len(data):
        b = data[i]
        if b == ESCAPE:
            i += 2
            continue
        if b == DELIMITER:
            end = i
            break
        i += 1
    if end is None:
        return None

    try:
        inner = _byte_unstuff(data[1:end])
        if len(inner) < 8:
            return None
        direction = inner[4]
        if direction not in Direction._VALID:
            return None
        msg_id = struct.unpack_from("<I", inner, 0)[0]
        op_code = inner[5]
        length = struct.unpack_from("<H", inner, 6)[0]
        payload_end = 8 + length
        if len(inner) < payload_end + 1:
            return None
        payload = inner[8:payload_end]
        checksum = inner[payload_end]
        calc_checksum = sum(inner[5:payload_end]) % 256
        if checksum != calc_checksum:
            return None
    except (struct.error, IndexError):
        return None

    return XlinkFrame(msg_id=msg_id, direction=direction, op_code=op_code, payload=payload)
