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
`set_power` (`0xD0`) and `set_brightness` (both the `0xF0` and `0xD2` forms) are
confirmed. Colour temperature and RGB are not - they ride the same `0xF0`
family whose brightness member works, so they are better founded than a guess,
but nobody has moved either over this transport.

A surprise came out of testing brightness, and it is recorded rather than
tidied away. **Both** forms changed the brightness of the same wired dimmer:
`0xF0` with the six-byte payload, and `0xD2` with a bare brightness byte -
even though `docs/mesh_opcodes.md` treats `0xD2` as the sol-lamp variant and
`devices.py` only ever sends it to sol lamps over TCP.

Accepted is not the same as equivalent, so the `is_sol_lamp` branch stays and
the default remains whichever form `devices.py` sends for that device class.
The verification channel here is cync-lan's own reporting, which surfaces the
brightness level and little else - it would not reveal a difference in fade
behaviour, in what the device persists across a power cycle, or in sub-percent
handling (`set_fine_brightness` extends the `0xE2` family precisely because the
basic form cannot express it). Sending a device the command its own class is
documented to use costs nothing and forecloses a whole category of side effect
nobody has looked for.

**Notifications work, and an earlier version of this module said otherwise.**
That claim was wrong and is worth recording as such: it came from testing one
sequence and generalising from it.

BlueZ's `StartNotify` is refused - the device answers the CCCD write with GATT
`Unlikely Error`, even though it does expose a `0x2902` descriptor. But the
CCCD is not how this protocol enables reporting. Writing `0x01` to the
notification characteristic's *value* is, and `google/python-dimond` does
exactly that and never touches the CCCD at all. With the enable-write first,
16 status packets arrived and decrypted correctly on hardware whose
`StartNotify` had just been rejected.

`subscribe()` therefore performs the enable-write first and treats a refused
subscribe as survivable rather than fatal.

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
    "OP_SET_LEVEL",
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
# 0xF0 carries brightness, temperature and RGB for ordinary devices; 0xD2/0xE2
# are the sol-lamp variants of the first two. Mirrors devices.py's own
# `op = 0xD2 if self.is_sol_lamp else 0xF0` rather than reimplementing the rule.
OP_SET_LEVEL = 0xF0
OP_SET_BRIGHTNESS_SOL = 0xD2
OP_SET_TEMP_SOL = 0xE2
OP_STATUS_NOTIFY = 0xDC

# Aliases kept because acync names these as *the* brightness and temperature
# opcodes, and this module followed it at first. They are the sol-lamp forms.
#
# Do not assume they are inert on other hardware: 0xD2 was tested on a wired
# dimmer and DID change its brightness, which docs/mesh_opcodes.md's sol-lamp
# framing does not predict. See set_brightness for why the branch is kept
# anyway.
OP_SET_BRIGHTNESS = OP_SET_BRIGHTNESS_SOL
OP_SET_TEMP_RGB = OP_SET_TEMP_SOL

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

    Two four-byte slots per packet, laid out `[id, presence, brightness, extra]`.

    **The presence rule here is the opposite of acync's, on purpose.** acync
    skips a slot whose second byte is zero, treating it as absent. On the
    hardware this was captured from, that discards exactly the slots carrying
    real state and keeps the empty ones: across 17 packets, all nine slots with
    `byte[1] == 0` held plausible values (brightness 100, extra 255) while all
    twenty-five with `byte[1] != 0` were `brightness=0, extra=0` with a
    byte[1] that varied like noise.

    So a zero second byte is treated as *data-bearing*. This is **plausible,
    not confirmed** - it rests on one capture from one mesh, and it contradicts
    an implementation known to work elsewhere, which is exactly the kind of
    disagreement worth flagging rather than resolving by assertion. The
    `byte[1] != 0` records remain unexplained; they may be a different record
    type sharing the `0xDC` opcode.

    Brightness above 127 flags an RGB device and carries the colour packed into
    the next byte.
    """
    if len(plaintext) < 18 or plaintext[7] != OP_STATUS_NOTIFY:
        return []

    out: list[DeviceStatus] = []
    for offset in (10, 14):
        slot = plaintext[offset : offset + 4]
        # See the docstring: a zero second byte marks the data-bearing case on
        # the captured hardware, inverting acync's rule. An all-zero slot is
        # genuinely nothing.
        if len(slot) < 4 or slot[1] != 0 or slot == b"\x00\x00\x00\x00":
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

        # Order matters, and it is the opposite of the obvious one.
        #
        # Writing 0x01 to the notification characteristic's VALUE is the
        # vendor's own start-reporting command - google/python-dimond does
        # exactly this and never writes a CCCD at all. Notifications then flow.
        # BlueZ's StartNotify additionally writes the CCCD (0x2902, which this
        # hardware does expose, at handle 19) and the device answers that with
        # GATT 'Unlikely Error'.
        #
        # So the enable-write goes first and its failure is what matters. The
        # subscribe is attempted afterwards because bleak needs it to route
        # notifications to a callback, but a rejection there is survivable:
        # confirmed on hardware, 16 status packets arrived and decrypted
        # correctly on a connection whose StartNotify had been refused.
        try:
            await self._client.write_gatt_char(
                NOTIFICATION_CHAR, bytes([0x01]), response=True
            )
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "%s: could not enable status reporting (%s). Sending is unaffected.",
                self._mac,
                exc,
            )
            return False

        try:
            await self._client.start_notify(NOTIFICATION_CHAR, _on_notify)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "%s: StartNotify refused (%s). Reporting is enabled regardless - "
                "this firmware rejects the CCCD write but still sends notifications.",
                self._mac,
                exc,
            )

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

    async def set_brightness(
        self, target: int, brightness: int, *, is_sol_lamp: bool = False
    ) -> None:
        """Confirmed on hardware, in both forms - which was not expected.

        0xF0 with the six-byte payload changed a real wired dimmer's brightness,
        verified through cync-lan's own reporting over TCP. So did 0xD2 with a
        bare brightness byte, on the same device, even though that is documented
        as the sol-lamp variant and devices.py only sends it to sol lamps.

        The branch is kept regardless. Both being accepted does not make them
        equivalent, and the verification channel would not have shown the
        difference if there is one: cync-lan reports the brightness level, not
        fade behaviour, not what survives a power cycle, not sub-percent
        precision. Sending each device class the form it is documented to use
        costs nothing and avoids a category of side effect nobody has examined.

        `is_sol_lamp` mirrors devices.py's own discriminator; pass the device's
        existing flag rather than guessing at it here.
        """
        bri = max(0, min(100, brightness))
        if is_sol_lamp:
            await self.send(target, OP_SET_BRIGHTNESS_SOL, bytes([bri, 0x00, 0x00]))
        else:
            await self.send(
                target, OP_SET_LEVEL, bytes([0x01, bri, 0xFF, 0xFF, 0xFF, 0xFF])
            )

    async def set_colour_temp(
        self, target: int, colour_temp: int, *, is_sol_lamp: bool = False
    ) -> None:
        """NOT confirmed over BLE, though the payloads now match devices.py.

        Brightness in this same 0xF0 family is confirmed, so this is better
        founded than a guess - but nobody has moved a colour temperature over
        this transport.
        """
        temp = colour_temp & 0xFF
        if is_sol_lamp:
            await self.send(target, OP_SET_TEMP_SOL, bytes([0x05, temp, 0x00]))
        else:
            await self.send(
                target, OP_SET_LEVEL, bytes([0x01, 0xFF, temp, 0x00, 0x00, 0x00])
            )

    async def set_rgb(self, target: int, red: int, green: int, blue: int) -> None:
        """NOT confirmed over BLE.

        No sol-lamp variant exists for this one - devices.py sends 0xF0
        unconditionally.
        """
        await self.send(
            target,
            OP_SET_LEVEL,
            bytes([0x01, 0xFF, 0xFE, red & 0xFF, green & 0xFF, blue & 0xFF]),
        )
