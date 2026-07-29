"""Tests for the BLE mesh transport.

The interesting ones cross-check against `acync` (juanboro/cync2mqtt,
Apache-2.0, itself descended from google/python-dimond and python-tikteck).
That is an independent implementation from a different source lineage which
demonstrably drives real hardware, so agreeing with it byte-for-byte is
evidence rather than a restatement of our own assumptions - which is exactly
what a test written from the same source as the code under test would be.

The acync algorithms are transcribed literally below, in their original
list-of-ints style, so a future edit to ble_mesh cannot quietly drag the
oracle along with it.
"""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from cync_lan.ble_mesh import (
    OP_SET_POWER,
    VENDOR_ID,
    BleMeshError,
    BleMeshSession,
    DeviceStatus,
    build_command,
    decrypt_packet,
    encrypt_packet,
    mac_to_address,
    mesh_credentials_from_home,
    parse_status,
)
from cync_lan.ble_provision import generate_sk

MESH_NAME = "30C2BC4ABC3D"
MESH_PASSWORD = "0123456789abcdef"
MAC = "F4:BC:DA:33:52:66"


# --------------------------------------------------------------------------
# acync, transcribed literally, as an independent oracle.
# --------------------------------------------------------------------------


def _acync_encrypt(key: list[int], data: list[int]) -> list[int]:
    cipher = Cipher(algorithms.AES(bytes(reversed(key))), modes.ECB()).encryptor()
    return list(reversed(list(cipher.update(bytes(reversed(data))))))


def _acync_encrypt_packet(sk: list[int], address: list[int], packet: list[int]):
    auth_nonce = [
        address[0],
        address[1],
        address[2],
        address[3],
        0x01,
        packet[0],
        packet[1],
        packet[2],
        15,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
    ]
    authenticator = _acync_encrypt(sk, auth_nonce)
    for i in range(15):
        authenticator[i] = authenticator[i] ^ packet[i + 5]
    mac = _acync_encrypt(sk, authenticator)
    for i in range(2):
        packet[i + 3] = mac[i]
    iv = [
        0,
        address[0],
        address[1],
        address[2],
        address[3],
        0x01,
        packet[0],
        packet[1],
        packet[2],
        0,
        0,
        0,
        0,
        0,
        0,
        0,
    ]
    temp = _acync_encrypt(sk, iv)
    for i in range(15):
        packet[i + 5] ^= temp[i]
    return packet


def _session_key() -> bytes:
    return generate_sk(
        MESH_NAME.encode(), MESH_PASSWORD.encode(), bytes(range(8)), bytes(range(8, 16))
    )


# --------------------------------------------------------------------------


def test_encrypt_packet_matches_acync_byte_for_byte():
    """The load-bearing test: our packet cipher against a working one."""
    sk = _session_key()
    address = mac_to_address(MAC)
    ours = encrypt_packet(sk, address, build_command(7, 37, OP_SET_POWER, bytes([1])))
    theirs = _acync_encrypt_packet(
        list(sk), list(address), list(build_command(7, 37, OP_SET_POWER, bytes([1])))
    )
    assert bytes(ours) == bytes(theirs)


def test_vendor_id_occupies_its_own_field():
    """`0x11, 0x02` is the vendor ID, not a payload prefix.

    Over TCP the same two bytes lead the payload (docs/mesh_opcodes.md); here
    they get a field, and the payload carries arguments only. Confirmed on
    hardware - inbound traffic decoded with the vendor at exactly this offset.
    """
    packet = build_command(1, 37, OP_SET_POWER, bytes([1]))
    assert packet[8] == VENDOR_ID & 0xFF == 0x11
    assert packet[9] == (VENDOR_ID >> 8) & 0xFF == 0x02
    assert packet[10] == 1, "argument follows the vendor field, not a repeated prefix"


def test_command_layout():
    packet = build_command(0x1234, 0x0025, OP_SET_POWER, bytes([1]))
    assert len(packet) == 20
    assert (packet[0], packet[1]) == (0x34, 0x12), "counter is little-endian"
    assert (packet[5], packet[6]) == (0x25, 0x00), "target is little-endian"
    assert packet[7] == OP_SET_POWER


def test_decrypt_packet_is_its_own_inverse():
    """It is an XOR keystream, and NOT the inverse of encrypt_packet.

    The two directions use different IV layouts. Asserting round-trip against
    encrypt_packet would be the natural mistake and would fail for the right
    reason; this asserts the property that actually holds.
    """
    sk = _session_key()
    address = mac_to_address(MAC)
    data = bytearray(range(20))
    once = decrypt_packet(sk, address, bytearray(data))
    twice = decrypt_packet(sk, address, bytearray(once))
    assert bytes(twice) == bytes(data)
    assert bytes(once) != bytes(data)


def test_encrypt_packet_rejects_wrong_length():
    with pytest.raises(BleMeshError, match="20 bytes"):
        encrypt_packet(_session_key(), mac_to_address(MAC), bytearray(19))


def test_build_command_rejects_oversized_payload():
    with pytest.raises(BleMeshError, match="too long"):
        build_command(1, 1, OP_SET_POWER, bytes(11))


def test_mac_to_address_reverses_byte_order():
    assert mac_to_address("AA:BB:CC:DD:EE:FF") == bytes(
        [0xFF, 0xEE, 0xDD, 0xCC, 0xBB, 0xAA]
    )


def test_mac_to_address_rejects_rubbish():
    with pytest.raises(BleMeshError):
        mac_to_address("not-a-mac")


# --------------------------------------------------------------------------
# Credentials come from the cloud export, never the hub.
# --------------------------------------------------------------------------


def test_mesh_credentials_are_home_mac_and_access_key():
    """Confirmed on hardware: a handshake built from these verified."""
    name, password = mesh_credentials_from_home(
        {"mac": "30C2BC4ABC3D", "access_key": 12345, "id": 999}
    )
    assert name == "30C2BC4ABC3D"
    assert password == "12345", "access_key is used as a string even when numeric"


def test_mesh_credentials_error_names_the_missing_field():
    with pytest.raises(BleMeshError, match="access_key"):
        mesh_credentials_from_home({"mac": "30C2BC4ABC3D"})


# --------------------------------------------------------------------------
# Status parsing.
# --------------------------------------------------------------------------


def test_parse_status_reads_both_slots():
    packet = bytearray(20)
    packet[7] = 0xDC
    packet[10:14] = bytes([37, 1, 50, 200])
    packet[14:18] = bytes([38, 1, 0, 100])
    statuses = parse_status(bytes(packet))
    assert statuses == [
        DeviceStatus(device_id=37, brightness=50, is_rgb=False, colour_temp=200),
        DeviceStatus(device_id=38, brightness=0, is_rgb=False, colour_temp=100),
    ]


def test_parse_status_skips_empty_slots():
    """Second byte zero means 'no device here', not 'a device reporting 0'."""
    packet = bytearray(20)
    packet[7] = 0xDC
    packet[10:14] = bytes([37, 0, 50, 200])
    packet[14:18] = bytes([38, 1, 25, 100])
    assert [s.device_id for s in parse_status(bytes(packet))] == [38]


def test_parse_status_decodes_rgb_when_brightness_flags_it():
    packet = bytearray(20)
    packet[7] = 0xDC
    packet[10:14] = bytes([37, 1, 128 + 60, 0xFF])
    (status,) = parse_status(bytes(packet))
    assert status.is_rgb and status.brightness == 60
    assert (status.red, status.green, status.blue) == (255, 255, 255)


def test_parse_status_ignores_other_opcodes():
    packet = bytearray(20)
    packet[7] = 0xEA  # seen in real captures alongside status
    packet[10:14] = bytes([37, 1, 50, 200])
    assert parse_status(bytes(packet)) == []


# --------------------------------------------------------------------------
# Session behaviour, against a fake client.
# --------------------------------------------------------------------------


class FakeClient:
    """Enough of a GATT client to drive the session, per the GattClient Protocol."""

    def __init__(self, pairing_response: bytes, notify_raises: bool = False):
        self._pairing_response = pairing_response
        self._notify_raises = notify_raises
        self.connected = True
        self.writes: list[tuple[str, bytes, bool]] = []

    @property
    def is_connected(self) -> bool:
        return self.connected

    async def read_gatt_char(self, char_specifier: str, **kwargs):
        return bytearray(self._pairing_response)

    async def write_gatt_char(self, char_specifier, data, response=False, **kwargs):
        self.writes.append((char_specifier, bytes(data), response))

    async def start_notify(self, char_specifier, callback, **kwargs):
        if self._notify_raises:
            raise RuntimeError("GATT Protocol Error: Unlikely Error")


def _valid_pairing_response(r_app: bytes) -> bytes:
    """What a device that really derived the same key would send back."""
    from cync_lan.ble_provision import _pad16, key_encrypt

    r_dev = bytes(range(0x20, 0x28))
    proof = key_encrypt(MESH_NAME.encode(), MESH_PASSWORD.encode(), _pad16(r_dev))[:8]
    return bytes([0x0C]) + r_dev + proof


@pytest.mark.asyncio
async def test_authenticate_verifies_the_device_proof():
    r_app = bytes(range(8))
    client = FakeClient(_valid_pairing_response(r_app))
    session = BleMeshSession(client, MAC, MESH_NAME, MESH_PASSWORD)
    assert await session.authenticate(r_app=r_app) is True
    assert session.authenticated


@pytest.mark.asyncio
async def test_authenticate_reports_a_bad_proof_without_raising():
    """A wrong password still derives a key - only the proof catches it."""
    client = FakeClient(bytes([0x0C]) + bytes(range(0x20, 0x28)) + b"\x00" * 8)
    session = BleMeshSession(client, MAC, MESH_NAME, MESH_PASSWORD)
    assert await session.authenticate(r_app=bytes(range(8))) is False


@pytest.mark.asyncio
async def test_authenticate_raises_on_a_truncated_response():
    session = BleMeshSession(FakeClient(b"\x0c\x01"), MAC, MESH_NAME, MESH_PASSWORD)
    with pytest.raises(BleMeshError, match="too short"):
        await session.authenticate(r_app=bytes(range(8)))


@pytest.mark.asyncio
async def test_send_requires_authentication():
    session = BleMeshSession(FakeClient(b""), MAC, MESH_NAME, MESH_PASSWORD)
    with pytest.raises(BleMeshError, match="authenticate"):
        await session.send(37, OP_SET_POWER, bytes([1]))


@pytest.mark.asyncio
async def test_send_refuses_a_dropped_link():
    r_app = bytes(range(8))
    client = FakeClient(_valid_pairing_response(r_app))
    session = BleMeshSession(client, MAC, MESH_NAME, MESH_PASSWORD)
    await session.authenticate(r_app=r_app)
    client.connected = False
    with pytest.raises(BleMeshError, match="link is down"):
        await session.set_power(37, True)


@pytest.mark.asyncio
async def test_set_power_writes_to_the_control_characteristic():
    from cync_lan.ble_mesh import CONTROL_CHAR

    r_app = bytes(range(8))
    client = FakeClient(_valid_pairing_response(r_app))
    session = BleMeshSession(client, MAC, MESH_NAME, MESH_PASSWORD)
    await session.authenticate(r_app=r_app)
    await session.set_power(37, True)

    char, data, response = client.writes[-1]
    assert char == CONTROL_CHAR
    assert len(data) == 20
    assert response is False, "control writes are fire-and-forget"


@pytest.mark.asyncio
async def test_counter_advances_so_packets_differ():
    """Two identical commands must not produce identical ciphertext."""
    r_app = bytes(range(8))
    client = FakeClient(_valid_pairing_response(r_app))
    session = BleMeshSession(client, MAC, MESH_NAME, MESH_PASSWORD)
    await session.authenticate(r_app=r_app)
    await session.set_power(37, True)
    await session.set_power(37, True)
    assert client.writes[-1][1] != client.writes[-2][1]


@pytest.mark.asyncio
async def test_subscribe_failure_is_not_fatal():
    """Real firmware declares notify, refuses the CCCD write, drops the link.

    Sending must survive that, because control writes go to a different
    characteristic entirely.
    """
    r_app = bytes(range(8))
    client = FakeClient(_valid_pairing_response(r_app), notify_raises=True)
    session = BleMeshSession(client, MAC, MESH_NAME, MESH_PASSWORD)
    await session.authenticate(r_app=r_app)

    assert await session.subscribe(lambda statuses: None) is False
    assert session.notifications_active is False
    await session.set_power(37, True)  # must still work
