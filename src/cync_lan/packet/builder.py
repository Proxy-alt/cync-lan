import struct

__all__ = [
    "PacketBuilder",
]
class PacketBuilder:
    DATA_BOUNDARY = 0x7E

    # Phone app packet families
    APP_AUTH_HEADER = (0x13, 0x00, 0x00, 0x00)
    # app and device both use A3, so dont depend on it for app/device ID
    APP_CONNECT_HEADER = (0xA3, 0x00, 0x00, 0x00)
    # The header byte is bit-packed (confirmed against the app's own Xlink
    # SDK: type = (b>>4)&0xF, response = (b>>3)&1, version = b&0x7) rather
    # than a flat enum - 0x13 (type=1, response=0, version=3) and 0x10
    # (type=1, response=0, version=0) are the SAME "login" request family at
    # different protocol versions. Only 0x10 and 0x13 have actually been
    # observed on the wire; the other in-between version bytes (0x11, 0x12)
    # are left unrecognized rather than assumed identical.
    APP_REQUEST_HEADERS = (0x13, 0x10)
    APP_AUTH_RESPONSE = (0x18, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00)
    APP_RESPONSE_HEADERS = (0x18,)
    APP_HEADERS = APP_REQUEST_HEADERS + APP_RESPONSE_HEADERS

    # Device packet families
    DEVICE_REQUEST_X23 = (0x23,)
    DEVICE_REQUEST_XC3 = (0xC3,)
    DEVICE_REQUEST_XD3 = (0xD3,)
    DEVICE_REQUEST_X83 = (0x83,)
    DEVICE_REQUEST_X73 = (0x73,)
    DEVICE_REQUEST_X7B = (0x7B,)
    DEVICE_REQUEST_X43 = (0x43,)
    DEVICE_REQUEST_XA3 = (0xA3,)
    DEVICE_REQUEST_XAB = (0xAB,)
    DEVICE_REQUEST_HEADERS = (0x23, 0xC3, 0xD3, 0x83, 0x73, 0x7B, 0x43, 0xA3, 0xAB)

    DEVICE_RESPONSE_AUTH_ACK = (0x28, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00)
    # This is a server->device response to the device's own 0xC3 "connection
    # request" - not implemented anywhere in the Cync Android app (it only
    # speaks the 0x13/0x18 "app" packet family), so there's no app-side class
    # to read directly. Diffing this constant against two other real captures
    # of the same ack from different devices 6 seconds apart (see
    # docs/debugging_sessions/3 devices/Plug - Toggle Power/{Bulb,Plug}.md)
    # strongly suggests the 11-byte body is a server-pushed current-time
    # struct: [0]=0x0D fixed tag (meaning unknown) [1:3]=year BE (0x07E8=2024)
    # [3]=month [4]=day [5]=day-of-week (Sunday=1 convention - this constant's
    # day/dow bytes, 10/Sun, are internally consistent: 2024-03-10 was a
    # Sunday) [6]=hour [7]=minute [8]=second (confirmed: this is the one byte
    # that differed between the two real captures, by exactly their 6-second
    # capture gap) [9:11]=unconfirmed, identical across all known samples.
    # Medium-high confidence, not proven - the hour byte was off by one from
    # the real captures' displayed capture time (plausibly a timezone
    # artifact), and bytes 9-10 have no independent samples to disambiguate.
    # Hardcoded and already working for local emulation; no code needs to
    # build this dynamically today.
    DEVICE_RESPONSE_CONNECTION_ACK = (
        0xC8,
        0x00,
        0x00,
        0x00,
        0x0B,
        0x0D,
        0x07,
        0xE8,
        0x03,
        0x0A,
        0x01,
        0x0C,
        0x04,
        0x1F,
        0xFE,
        0x0C,
    )
    DEVICE_RESPONSE_X48_ACK = (0x48, 0x00, 0x00, 0x00, 0x03, 0x01, 0x01, 0x00)
    DEVICE_RESPONSE_X88_ACK = (0x88, 0x00, 0x00, 0x00, 0x03, 0x00, 0x00, 0x00)
    DEVICE_RESPONSE_PING_ACK = (0xD8, 0x00, 0x00, 0x00, 0x00)
    DEVICE_RESPONSE_X78_BASE = (0x78, 0x00, 0x00, 0x00)
    DEVICE_RESPONSE_X7B_BASE = (0x7B, 0x00, 0x00, 0x00)

    DEVICE_HEADERS = (0x23, 0xC3, 0xD3, 0x83, 0x73, 0x7B, 0x78, 0x43, 0xA3, 0xAB)
    ALL_HEADERS = DEVICE_HEADERS + APP_REQUEST_HEADERS + APP_RESPONSE_HEADERS

    @staticmethod
    def _require_len(name: str, data: bytes, expected_len: int) -> None:
        if len(data) != expected_len:
            raise ValueError(
                f"{name} must be exactly {expected_len} bytes, got {len(data)}"
            )

    @staticmethod
    def _require_u8(name: str, value: int) -> None:
        if not isinstance(value, int) or not (0 <= value <= 0xFF):
            raise ValueError(f"{name} must be an integer between 0 and 255, got {value!r}")

    @classmethod
    def is_device_request(cls, packet_type: int) -> bool:
        return packet_type in cls.DEVICE_REQUEST_HEADERS

    @classmethod
    def is_app_request(cls, packet_type: int) -> bool:
        return packet_type in cls.APP_HEADERS

    @staticmethod
    def build_23_ack() -> bytes:
        return bytes(PacketBuilder.DEVICE_RESPONSE_AUTH_ACK)

    @staticmethod
    def build_a3_ack(queue_id: bytes, msg_id: bytes) -> bytes:
        """Respond to a 0xA3 packet from the device."""
        PacketBuilder._require_len("queue_id", queue_id, 4)
        PacketBuilder._require_len("msg_id", msg_id, 3)
        payload = b"xlink_dev" + bytes(948) + b"\xe3\x4f\x02\x10"
        total_len = len(queue_id) + len(msg_id) + len(payload)
        length_factor, length_byte = divmod(total_len, 256)
        header = b"\xab\x00\x00" + bytes([length_factor, length_byte])
        return header + queue_id + msg_id + payload

    @staticmethod
    def build_a3_control_request(queue_id: bytes, msg_id: bytes) -> bytes:
        """Build the 0xA3 packet that enables control for a device session."""
        PacketBuilder._require_len("queue_id", queue_id, 4)
        PacketBuilder._require_len("msg_id", msg_id, 3)
        return bytes([0xA3, 0x00, 0x00, 0x00, 0x07]) + queue_id + msg_id

    @staticmethod
    def build_c3_ack() -> bytes:
        return bytes(PacketBuilder.DEVICE_RESPONSE_CONNECTION_ACK)

    @staticmethod
    def build_d3_ack() -> bytes:
        return bytes(PacketBuilder.DEVICE_RESPONSE_PING_ACK)

    @staticmethod
    def build_43_ack(msg_id: bytes) -> bytes:
        """Respond to a 0x43 packet from the device."""
        PacketBuilder._require_len("msg_id", msg_id, 3)
        return bytes([0x48, 0x00, 0x00, 0x00, 0x03]) + msg_id[:-1] + b"\x00"

    @staticmethod
    def build_73_ack(queue_id: bytes, msg_id: bytes) -> bytes:
        """Respond to a 0x73 packet from the device."""
        PacketBuilder._require_len("queue_id", queue_id, 4)
        PacketBuilder._require_len("msg_id", msg_id, 3)
        return struct.pack(">BBBBB", 0x78, 0x00, 0x00, 0x00, 0x07) + queue_id + msg_id

    @staticmethod
    def build_83_ack(msg_id: bytes) -> bytes:
        """Respond to a 0x83 packet from the device."""
        PacketBuilder._require_len("msg_id", msg_id, 3)
        return bytes([0x88, 0x00, 0x00, 0x00, 0x03]) + msg_id

    @staticmethod
    def build_mesh_info_request(queue_id: bytes, msg_id: bytes = b"\x00\x00\x00") -> bytes:
        """Build the 0x73 request that asks a bridge for mesh status info."""
        PacketBuilder._require_len("queue_id", queue_id, 4)
        PacketBuilder._require_len("msg_id", msg_id, 3)
        inner_packet = bytes(
            [
                PacketBuilder.DATA_BOUNDARY,
                0x1F,
                0x00,
                0x00,
                0x00,
                0xF8,
                0x52,
                0x06,
                0x00,
                0x00,
                0x00,
                0xFF,
                0xFF,
                0x00,
                0x00,
                0x56,
                PacketBuilder.DATA_BOUNDARY,
            ]
        )
        return PacketBuilder.build_outer_packet(0x73, queue_id, inner_packet, msg_id=msg_id)

    @staticmethod
    def build_mesh_status_ack(queue_id: bytes, msg_id: bytes = b"\x00\x00\x00") -> bytes:
        """Build ACK packet sent after processing a mesh info page (0xF8 0xAF)."""
        PacketBuilder._require_len("queue_id", queue_id, 4)
        PacketBuilder._require_len("msg_id", msg_id, 3)
        inner_packet = bytes(
            [
                PacketBuilder.DATA_BOUNDARY,
                0x1E,
                0x00,
                0x00,
                0x00,
                0xF8,
                0xAF,
                0x02,
                0x00,
                0xAF,
                0x01,
                0x61,
                PacketBuilder.DATA_BOUNDARY,
            ]
        )
        return PacketBuilder.build_outer_packet(0x73, queue_id, inner_packet, msg_id=msg_id)

    @staticmethod
    def build_control_packet(
            msg_id: int,
            target_id: int,
            sub_id: int,
            op_code: int,
            cmd_code: int,
            command_payload: bytes,
            repeat_op_code: bool = True,
    ) -> bytes:
        """Builds the inner 0x7E bound packet structure.

        repeat_op_code: every op family confirmed against real hardware so
        far (0xD0/0xF0/0xE2/...) repeats op_code as a standalone byte right
        before command_payload - keep that as the default. The 0x8E
        "mesh-relay" op family (real command classes route through it via a
        hardcoded dispatch, see set_indicator_led/set_motion_sensor_settings/
        execute_scene) does NOT: verified against a real captured packet
        (docs/debugging_sessions/3 devices/Plug - Toggle Power/Plug.md) whose
        checksum only balances when no extra byte is inserted there - for
        that family the payload's own leading byte (not a repeat of op_code)
        follows routing directly. Pass repeat_op_code=False for 0x8E-family
        callers.
        """
        PacketBuilder._require_u8("msg_id", msg_id)
        PacketBuilder._require_u8("target_id", target_id)
        PacketBuilder._require_u8("sub_id", sub_id)
        PacketBuilder._require_u8("op_code", op_code)
        if not isinstance(command_payload, bytes):
            raise TypeError(
                f"command_payload must be bytes, got {type(command_payload)!r}"
            )

        # Header: msg_id (1 byte), 3 null padding bytes, 0xF8, op_code, 0x0D, 0x00
        # Format >BxxxBBBB = 1 + 3 + 1 + 1 + 1 + 1 = 8 bytes total
        header = struct.pack(">B xxx B B B B", msg_id, 0xF8, op_code, cmd_code, 0x00)
        # Routing: msg_id (1 byte), 4 null padding bytes, target_id, sub_id
        # Format >BxxxxBB = 1 + 4 + 1 + 1 = 7 bytes total
        routing = struct.pack(">B xxxx B B", msg_id, target_id, sub_id)
        op_prefix = struct.pack(">B", op_code) if repeat_op_code else b""
        inner_data = header + routing + op_prefix + command_payload
        checksum = sum(inner_data[5:]) % 256
        return (struct.pack(">B",PacketBuilder.DATA_BOUNDARY) + inner_data +
                struct.pack(">BB", checksum, PacketBuilder.DATA_BOUNDARY))

    @staticmethod
    def build_outer_packet(
        packet_type: int,
        queue_id: bytes,
        inner_packet: bytes,
        msg_id: bytes = b"\x00\x00\x00",
    ) -> bytes:
        """Builds the outer TCP packet (e.g., 0x73 commands)"""
        PacketBuilder._require_u8("packet_type", packet_type)
        PacketBuilder._require_len("queue_id", queue_id, 4)
        PacketBuilder._require_len("msg_id", msg_id, 3)
        if not isinstance(inner_packet, bytes):
            raise TypeError(f"inner_packet must be bytes, got {type(inner_packet)!r}")

        packet_length = len(queue_id) + 3 + len(inner_packet)
        length_multiplier, length_remainder = divmod(packet_length, 256)
        # 5 byte header
        header = struct.pack(">BBBBB", packet_type, 0x00, 0x00, length_multiplier, length_remainder)

        return header + queue_id + msg_id + inner_packet
