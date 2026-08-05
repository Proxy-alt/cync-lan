"""Decode ATT traffic straight off the HCI monitor socket - a tiny btmon.

Exists to answer one question: when BlueZ's StartNotify fails with ATT error
0x0E ("Unlikely Error"), does that error actually arrive **on the air from the
peer**, or does BlueZ manufacture it locally when its own descriptor write goes
unanswered?

That distinction decides whether the notify characteristic really has a CCCD.
An error response is proof an attribute exists and chose to reject; silence on
the wire with an error appearing only in BlueZ's D-Bus reply means BlueZ wrote
into a handle the device does not have and invented the failure.

`HCI_CHANNEL_MONITOR` is passive - the same feed btmon consumes. It does not
take the adapter, so Home Assistant keeps running throughout.

Usage:  python3 att_monitor.py [seconds]
"""

from __future__ import annotations

import socket
import struct
import sys
import time

AF_BLUETOOTH = 31
BTPROTO_HCI = 1
HCI_CHANNEL_MONITOR = 2
HCI_DEV_NONE = 0xFFFF

# hci_mon_hdr opcodes we care about. TX is host -> controller (what we sent),
# RX is controller -> host (what the peer sent back).
OP_ACL_TX = 0x0004
OP_ACL_RX = 0x0005

ATT_CID = 0x0004

ATT_OPCODES = {
    0x01: "ERROR_RESPONSE",
    0x02: "EXCHANGE_MTU_REQ",
    0x03: "EXCHANGE_MTU_RSP",
    0x04: "FIND_INFORMATION_REQ",
    0x05: "FIND_INFORMATION_RSP",
    0x08: "READ_BY_TYPE_REQ",
    0x09: "READ_BY_TYPE_RSP",
    0x0A: "READ_REQ",
    0x0B: "READ_RSP",
    0x10: "READ_BY_GROUP_TYPE_REQ",
    0x11: "READ_BY_GROUP_TYPE_RSP",
    0x12: "WRITE_REQ",
    0x13: "WRITE_RSP",
    0x1B: "HANDLE_VALUE_NOTIFICATION",
    0x1D: "HANDLE_VALUE_INDICATION",
    0x52: "WRITE_CMD",
}

ATT_ERRORS = {
    0x01: "INVALID_HANDLE",
    0x02: "READ_NOT_PERMITTED",
    0x03: "WRITE_NOT_PERMITTED",
    0x0A: "ATTRIBUTE_NOT_FOUND",
    0x0E: "UNLIKELY_ERROR",
}


def describe(pdu: bytes) -> str:
    if not pdu:
        return "(empty)"
    op = pdu[0]
    name = ATT_OPCODES.get(op, f"op=0x{op:02x}")
    if op == 0x01 and len(pdu) >= 5:
        req, handle, err = pdu[1], struct.unpack("<H", pdu[2:4])[0], pdu[4]
        return (
            f"{name} req=0x{req:02x} handle=0x{handle:04x} "
            f"error=0x{err:02x} {ATT_ERRORS.get(err, '')}"
        )
    if op in (0x12, 0x52) and len(pdu) >= 3:
        handle = struct.unpack("<H", pdu[1:3])[0]
        return f"{name} handle=0x{handle:04x} value={pdu[3:].hex()}"
    if op == 0x1B and len(pdu) >= 3:
        handle = struct.unpack("<H", pdu[1:3])[0]
        return f"{name} handle=0x{handle:04x} len={len(pdu) - 3}"
    if op in (0x04, 0x0A) and len(pdu) >= 3:
        return f"{name} {pdu[1:].hex()}"
    return f"{name} {pdu[1:].hex()[:40]}"


def main() -> int:
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
    sock = socket.socket(AF_BLUETOOTH, socket.SOCK_RAW, BTPROTO_HCI)
    try:
        sock.bind((HCI_DEV_NONE, HCI_CHANNEL_MONITOR))
    except OSError as exc:
        print(f"cannot bind the monitor socket: {exc}")
        print("needs root and the host's Bluetooth namespace")
        return 2
    sock.settimeout(1.0)

    print(f"# monitoring ATT for {duration:.0f}s (passive, adapter untouched)")
    deadline = time.monotonic() + duration
    # ACL reassembly per connection handle - an ATT PDU can be split across
    # HCI fragments, and the interesting exchange is small enough that
    # dropping a continuation would silently lose exactly the packet in
    # question.
    pending: dict[int, tuple[int, bytearray]] = {}

    while time.monotonic() < deadline:
        try:
            data = sock.recv(4096)
        except TimeoutError:
            continue
        except OSError:
            break
        if len(data) < 6:
            continue
        opcode, _index, plen = struct.unpack("<HHH", data[:6])
        body = data[6 : 6 + plen]
        if opcode not in (OP_ACL_TX, OP_ACL_RX) or len(body) < 4:
            continue

        direction = "TX->dev" if opcode == OP_ACL_TX else "RX<-dev"
        handle_flags, dlen = struct.unpack("<HH", body[:4])
        conn = handle_flags & 0x0FFF
        pb = (handle_flags >> 12) & 0x3
        payload = body[4 : 4 + dlen]

        if pb == 0x1 and conn in pending:  # continuation
            want, buf = pending[conn]
            buf.extend(payload)
            if len(buf) < want:
                continue
            l2_payload, cid = bytes(buf[:want]), pending[conn][0]
            del pending[conn]
            # cid was stashed alongside; recompute below instead
            continue

        if len(payload) < 4:
            continue
        l2len, cid = struct.unpack("<HH", payload[:4])
        if cid != ATT_CID:
            continue
        pdu = payload[4 : 4 + l2len]
        if len(pdu) < l2len:
            pending[conn] = (l2len, bytearray(pdu))
            continue
        print(f"{time.strftime('%H:%M:%S')} conn=0x{conn:03x} {direction}  {describe(pdu)}")

    print("# done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
