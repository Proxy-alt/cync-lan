"""Telink BLE mesh transport - controlling already-provisioned devices.

The sibling of `ble_provision`, which handles getting a factory-default device
*onto* a mesh. This one talks to devices already on one, and is the second
transport for the same protocol `server.py` speaks over TCP.

CONFIRMED ON HARDWARE (2026-07-28)
----------------------------------
Not a translation of somebody else's code taken on faith. Against a wired Cync
switch, via `research`'s `probes/ble_control_probe.py`:

- the session handshake completed with `verify_pairing_response` reporting
  mutual auth verified - the device proved it derived the same key material;
- inbound traffic decrypted into sensible plaintext, with the vendor ID at
  bytes 8:9 exactly where the framing predicts and readable ASCII after it;
- a `set_power` built by this module's layout **changed the switch's state**,
  and cync-lan reported that change over its own TCP connection.

That last point is what makes it evidence rather than a hopeful reading: the
command left over Bluetooth and the confirmation arrived over TCP, so two
independent transports corroborate each other.

**Mesh relay is confirmed too.** A command addressed to one device, sent over
a connection to a *different* device, is relayed and acted on. So a consumer
needs ONE BLE connection to reach the whole mesh - not a connection per
device, which at ~40 nodes would be unworkable.

WHAT IS NOT CONFIRMED
---------------------
Only `set_power` (`0xD0`) has been exercised over this transport. Brightness,
temperature and RGB are carried here because the opcode table is shared with
the TCP path (see `docs/mesh_opcodes.md`), but they have not been tested over
BLE and should be treated as plausible rather than confirmed.

Notification subscription fails outright on at least one firmware: it declares
`notify` on the notify characteristic, rejects the CCCD write with GATT
`Unlikely Error`, and then drops the connection. `subscribe()` is therefore
optional and its failure is never fatal - sending does not depend on it.

WHY THIS MODULE NEVER IMPORTS BLEAK
-----------------------------------
It takes a client rather than creating one. That is the single most important
decision here.

A Home Assistant integration must be able to hand in a connection from HA's
own Bluetooth stack, which is what makes ESPHome Bluetooth proxies work. Had
this module constructed a `BleakClient` itself, proxies would be impossible
and the transport would only ever reach devices in radio range of the machine
running it - useless for a mesh spread across a house.

So the dependency is structural (`GattClient` below), satisfied by
`bleak.BleakClient` and by HA's client alike, and `bleak` stays an optional
extra rather than a hard requirement.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, Protocol, runtime_checkable

from .ble_provision import (
    _aes_ecb_encrypt,
    _pad16,
    derive_session_key,
    key_encrypt,
    verify_pairing_response,
)

logger = logging.getLogger(__name__)

__all__ = [
    "CONTROL_CHAR",
    "NOTIFICATION_CHAR",
    "PAIRING_CHAR",
    "VENDOR_ID",
    "BleMeshError",
    "BleMeshSession",
    "DeviceStatus",
    "GattClient",
    "build_command",
    "decrypt_packet",
    "encrypt_packet",
    "mac_to_address",
    "mesh_credentials_from_home",
]

# Telink mesh GATT characteristics. Confirmed present on real hardware, with
# 1911 read/write/notify, 1912 read/write/write-without-response, 1914
# read/write.
NOTIFICATION_CHAR = "00010203-0405-0607-0809-0a0b0c0d1911"
CONTROL_CHAR = "00010203-0405-0607-0809-0a0b0c0d1912"
PAIRING_CHAR = "00010203-0405-0607-0809-0a0b0c0d1914"

# The Telink vendor ID. This is the `0x11, 0x02` that opens every payload in
# docs/mesh_opcodes.md - the TCP transport embeds it at the head of the
# payload, while BLE gives it a field of its own. Same command, different
# framing, which is why the opcode table is shared between the two.
VENDOR_ID = 0x0211

OP_SET_POWER = 0xD0
OP_SET_BRIGHTNESS = 0xD2
OP_SET_TEMP_RGB = 0xE2
OP_STATUS_NOTIFY = 0xDC

_PAIRING_OPCODE = 0x0C
_PACKET_LEN = 20
_MAX_COUNTER = 0xFFFF


class BleMeshError(Exception):
    """Raised when a mesh session cannot be established or used."""


@runtime_checkable
class GattClient(Protocol):
    """The slice of a GATT client this module needs.

    Structural rather than nominal on purpose - see the module docstring.
    `bleak.BleakClient` satisfies it, and so does anything Home Assistant
    hands over, without this module importing either.
    """

    @property
    def is_connected(self) -> bool: ...

    async def read_gatt_char(self, char_specifier: str, **kwargs: Any) -> bytearray: ...

    async def write_gatt_char(
        self, char_specifier: str, data: bytes, response: bool = False, **kwargs: Any
    ) -> None: ...

    async def start_notify(
        self,
        char_specifier: str,
        callback: Callable[[Any, bytearray], Any],
        **kwargs: Any,
    ) -> None: ...


@dataclass(frozen=True)
class DeviceStatus:
    """One device's state, as reported in a `0xDC` status notification."""

    device_id: int
    brightness: int
    is_rgb: bool
    colour_temp: int = 0
    red: int = 0
    green: int = 0
    blue: int = 0


def mac_to_address(mac: str) -> bytes:
    """MAC string to the reversed byte order the packet cipher expects.

    Only the first four bytes are ever used, but all six are returned since
    the inbound direction indexes three of them.
    """
    try:
        return bytes(int(part, 16) for part in reversed(mac.split(":")))
    except ValueError as exc:
        raise BleMeshError(f"not a MAC address: {mac!r}") from exc


def mesh_credentials_from_home(home: dict) -> tuple[str, str]:
    """Pull the Telink mesh name and password out of an exported home.

    These do NOT come from the hub. `cloud_api._parse_raw_export` already
    writes them: the home's `mac` is the mesh name and its `access_key` is the
    mesh password. Both confirmed on hardware - a handshake built from them
    returned a verified mutual-auth proof.

    Worth stating explicitly because the integration's `query_mesh_credentials`
    button implies otherwise, and that button is a hub command - a family that
    currently gets no reply at all (see docs/hub_envelope_ab_test.md). Nothing
    here depends on it.
    """
    try:
        return str(home["mac"]), str(home["access_key"])
    except KeyError as exc:
        raise BleMeshError(
            f"exported home is missing {exc.args[0]!r}; expected both 'mac' and "
            "'access_key' as written by cloud_api._parse_raw_export"
        ) from exc


def encrypt_packet(session_key: bytes, address: bytes, packet: bytearray) -> bytearray:
    """Authenticate and encrypt one command packet, in place.

    Bytes 3-4 become a two-byte MAC over the plaintext; bytes 5.. are then
    XORed with a keystream block. Both halves derive from the session key and
    the connected device's address, so a packet is only valid on the link it
    was built for.
    """
    if len(packet) != _PACKET_LEN:
        raise BleMeshError(f"packet must be {_PACKET_LEN} bytes, got {len(packet)}")

    auth_nonce = (
        bytes(address[:4]) + b"\x01" + bytes(packet[0:3]) + bytes([15]) + b"\x00" * 7
    )
    authenticator = bytearray(_aes_ecb_encrypt(session_key, _pad16(auth_nonce)))
    for i in range(15):
        authenticator[i] ^= packet[i + 5]

    mac = _aes_ecb_encrypt(session_key, bytes(authenticator))
    packet[3] = mac[0]
    packet[4] = mac[1]

    iv = b"\x00" + bytes(address[:4]) + b"\x01" + bytes(packet[0:3]) + b"\x00" * 7
    keystream = _aes_ecb_encrypt(session_key, _pad16(iv))
    for i in range(15):
        packet[i + 5] ^= keystream[i]
    return packet


def decrypt_packet(session_key: bytes, address: bytes, packet: bytearray) -> bytearray:
    """Decrypt an inbound notification, in place.

    A different IV layout from the outbound direction, so this is not the
    inverse of `encrypt_packet`. It is its own inverse, being an XOR keystream
    - which the tests assert, because it is the property that actually holds
    and the easy mistake is to assume the other one.
    """
    iv = bytes(address[:3]) + bytes(packet[0:5]) + b"\x00" * 8
    keystream = _aes_ecb_encrypt(session_key, _pad16(b"\x00" + iv[:15]))
    for i in range(len(packet) - 7):
        packet[i + 7] ^= keystream[i]
    return packet


def build_command(counter: int, target: int, opcode: int, data: bytes) -> bytearray:
    """Lay out one plaintext mesh command.

    `target` is a mesh device id - `int(str(deviceID)[-3:])`, as
    `cloud_api._parse_raw_export` computes it. **0 is the broadcast address**
    and commands every device on the mesh at once; callers wanting one device
    must not pass it by accident.
    """
    if len(data) > _PACKET_LEN - 10:
        raise BleMeshError(f"payload too long for one packet: {len(data)} bytes")

    packet = bytearray(_PACKET_LEN)
    packet[0] = counter & 0xFF
    packet[1] = (counter >> 8) & 0xFF
    packet[5] = target & 0xFF
    packet[6] = (target >> 8) & 0xFF
    packet[7] = opcode
    # The vendor gets its own field here, unlike the TCP transport where the
    # same two bytes lead the payload.
    packet[8] = VENDOR_ID & 0xFF
    packet[9] = (VENDOR_ID >> 8) & 0xFF
    packet[10 : 10 + len(data)] = data
    return packet


def parse_status(plaintext: bytes) -> list[DeviceStatus]:
    """Decode the device reports carried in a decrypted `0xDC` notification.

    Two four-byte slots per packet. A slot whose second byte is zero is empty
    rather than a device reporting zero, so it is skipped. Brightness above
    127 flags an RGB device and carries the colour packed into the next byte.
    """
    if len(plaintext) < 18 or plaintext[7] != OP_STATUS_NOTIFY:
        return []

    out: list[DeviceStatus] = []
    for offset in (10, 14):
        slot = plaintext[offset : offset + 4]
        if len(slot) < 4 or slot[1] == 0:
            continue
        brightness = slot[2]
        if brightness >= 128:
            packed = slot[3]
            out.append(
                DeviceStatus(
                    device_id=slot[0],
                    brightness=brightness - 128,
                    is_rgb=True,
                    red=int(((packed & 0xE0) >> 5) * 255 / 7),
                    green=int(((packed & 0x1C) >> 2) * 255 / 7),
                    blue=int((packed & 0x03) * 255 / 3),
                )
            )
        else:
            out.append(
                DeviceStatus(
                    device_id=slot[0],
                    brightness=brightness,
                    is_rgb=False,
                    colour_temp=slot[3],
                )
            )
    return out


class BleMeshSession:
    """An authenticated session with one mesh node.

    Because commands are relayed, a single session reaches the whole mesh -
    confirmed on hardware. Consumers should hold one of these, not one per
    device.

    The client is supplied, never created: see the module docstring for why
    that is what makes Bluetooth proxies possible.
    """

    def __init__(
        self,
        client: GattClient,
        mac: str,
        mesh_name: str,
        mesh_password: str,
    ) -> None:
        self._client = client
        self._mac = mac
        self._address = mac_to_address(mac)
        self._mesh_name = mesh_name
        self._mesh_password = mesh_password
        self._session_key: Optional[bytes] = None
        self._counter = 1
        self._notify_active = False

    @property
    def authenticated(self) -> bool:
        return self._session_key is not None and self._client.is_connected

    @property
    def notifications_active(self) -> bool:
        """False on firmware that refuses the CCCD write - not an error."""
        return self._notify_active

    async def authenticate(self, r_app: Optional[bytes] = None) -> bool:
        """Perform the pairing handshake and derive the session key.

        `r_app` is the client's random contribution; a fresh one is generated
        per session unless supplied (tests supply it). Note this deliberately
        does not reuse `ble_provision.build_pairing_write`, which pins a fixed
        R_APP constant in order to reproduce the app's factory-default write
        byte for byte - correct there, wrong for a live session.

        Returns whether the device's proof verified. A False return is worth
        surfacing loudly: it means the mesh name or password is wrong, and
        every subsequent write will be ignored or the link dropped.
        """
        if r_app is None:
            import os

            r_app = os.urandom(8)

        proof = key_encrypt(
            self._mesh_name.encode("utf-8"),
            self._mesh_password.encode("utf-8"),
            _pad16(r_app),
        )[:8]
        await self._client.write_gatt_char(
            PAIRING_CHAR, bytes([_PAIRING_OPCODE]) + r_app + proof, response=True
        )
        response = bytes(await self._client.read_gatt_char(PAIRING_CHAR))

        if len(response) < 9:
            raise BleMeshError(
                f"pairing response too short ({len(response)} bytes) - the device "
                "did not accept the write"
            )

        self._session_key = derive_session_key(
            self._mesh_name, self._mesh_password, r_app, response[1:9]
        )

        # Deriving a key always succeeds, whatever password it is given. This
        # is the only thing that says the DEVICE agreed.
        verified = verify_pairing_response(
            self._mesh_name, self._mesh_password, response
        )
        if not verified:
            logger.warning(
                "%s: mesh mutual auth failed - the device's proof does not match. "
                "The mesh name should be the home's 'mac' and the password its "
                "'access_key'. Commands will very likely be ignored.",
                self._mac,
            )
        return verified

    async def subscribe(
        self, callback: Callable[[list[DeviceStatus]], Awaitable[None] | None]
    ) -> bool:
        """Try to receive status notifications. Optional, and often refused.

        At least one firmware declares `notify` on the characteristic, rejects
        the CCCD write with GATT `Unlikely Error`, and then drops the
        connection. So failure is reported, not raised, and a consumer that
        only sends never needs to call this at all.
        """
        if self._session_key is None:
            raise BleMeshError("authenticate() first")

        def _on_notify(_sender: Any, data: bytearray) -> None:
            assert self._session_key is not None
            plaintext = decrypt_packet(
                self._session_key, self._address, bytearray(data)
            )
            statuses = parse_status(bytes(plaintext))
            if statuses:
                result = callback(statuses)
                if hasattr(result, "__await__"):
                    import asyncio

                    asyncio.get_running_loop().create_task(result)  # type: ignore[arg-type]

        try:
            await self._client.start_notify(NOTIFICATION_CHAR, _on_notify)
            await self._client.write_gatt_char(
                NOTIFICATION_CHAR, bytes([0x01]), response=True
            )
        except Exception as exc:  # noqa: BLE001 - any GATT failure is non-fatal here
            logger.info(
                "%s: status notifications unavailable (%s). Sending is unaffected; "
                "this firmware declares notify but refuses the subscription.",
                self._mac,
                exc,
            )
            self._notify_active = False
            return False

        self._notify_active = True
        return True

    async def send(self, target: int, opcode: int, data: bytes) -> None:
        """Encrypt and write one command.

        Fire and forget - the control characteristic is written without a
        response, and this transport has no acknowledgement. Confirm state
        some other way; the TCP path's own reporting is one option, and is how
        this module was verified in the first place.
        """
        if self._session_key is None:
            raise BleMeshError("authenticate() first")
        if not self._client.is_connected:
            raise BleMeshError("link is down")

        packet = build_command(self._counter, target, opcode, data)
        self._counter = 1 if self._counter >= _MAX_COUNTER else self._counter + 1
        encrypted = encrypt_packet(self._session_key, self._address, packet)
        await self._client.write_gatt_char(
            CONTROL_CHAR, bytes(encrypted), response=False
        )

    async def set_power(self, target: int, on: bool) -> None:
        """Confirmed working on hardware."""
        await self.send(target, OP_SET_POWER, bytes([1 if on else 0]))

    async def set_brightness(self, target: int, brightness: int) -> None:
        """NOT confirmed over BLE - opcode shared with the TCP path only."""
        await self.send(
            target, OP_SET_BRIGHTNESS, bytes([max(0, min(100, brightness))])
        )

    async def set_colour_temp(self, target: int, colour_temp: int) -> None:
        """NOT confirmed over BLE - opcode shared with the TCP path only."""
        await self.send(target, OP_SET_TEMP_RGB, bytes([0x05, colour_temp & 0xFF]))

    async def set_rgb(self, target: int, red: int, green: int, blue: int) -> None:
        """NOT confirmed over BLE - opcode shared with the TCP path only."""
        await self.send(
            target,
            OP_SET_TEMP_RGB,
            bytes([0x04, red & 0xFF, green & 0xFF, blue & 0xFF]),
        )
