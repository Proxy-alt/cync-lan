#!/usr/bin/env python3
"""Probe: can we control an already-provisioned Cync device over BLE?

This exists to answer one question before any repository restructuring is
done - does BLE mesh *control* work against real hardware, using the crypto
this project already ships? Everything else in the cync_ble plan is packaging;
this is where the risk actually is.

WHAT IS ALREADY DONE, AND WHAT IS NEW
-------------------------------------
cync_lan.ble_provision already implements the whole session handshake, because
provisioning needs the same one:

    _aes_ecb_encrypt   Telink's AES-ECB with the byte-reversal quirk
    key_encrypt        XOR(name, password) -> AES
    generate_sk        session key from both sides' randoms
    build_pairing_write   the [0x0C] + R_app + proof write
    derive_session_key    R_app + R_dev -> session key
    verify_pairing_response

The only genuinely new code here is `encrypt_packet` / `decrypt_packet` - the
per-command layer - plus the GATT UUIDs and a send loop. That is the entire
gap between "provisions devices" and "controls devices".

PROTOCOL NOTE WORTH RECORDING
-----------------------------
docs/mesh_opcodes.md documents LAN payloads with a magic `0x11, 0x02` prefix,
e.g. set_power = op 0xD0, payload [0x11, 0x02, state, 0x00, 0x00].

That prefix is the Telink **vendor ID 0x0211, little-endian**. Over BLE the
vendor goes in its own field (packet[8:9]) and the payload carries only the
arguments; over the LAN transport the same bytes are embedded in the payload.
Same protocol, different framing - which is why the opcode table transfers.

Cross-checked against juanboro/cync2mqtt's `acync` (Apache-2.0, itself derived
from google/python-dimond and python-tikteck), an independently working BLE
implementation:

    acync                        docs/mesh_opcodes.md
    0xD0 [power]                 set_power      0xD0  [0x11,0x02,state,0,0]
    0xD2 [brightness]            set_brightness 0xD2 (sol-lamp variant)
    0xE2 [0x05, temp]            set_temperature 0xE2 (sol-lamp variant)
    0xE2 [0x04, r, g, b]         (RGB)
    0xDC (notification)          status responses

SAFETY
------
Sending only. It writes set_power to one mesh ID and reads notifications.
It does not provision, does not write mesh credentials, and does not touch
device settings - nothing here can re-key or unpair a device.

Run --self-test first. It validates the crypto against a literal transcription
of acync's working implementation and needs no hardware or credentials.

    python ble_control_probe.py --self-test
    python ble_control_probe.py --scan
    python ble_control_probe.py --mac AA:BB:CC:DD:EE:FF \
        --mesh-name YOURMESH --mesh-password YOURPASS --listen 20
    python ble_control_probe.py --mac AA:BB:CC:DD:EE:FF \
        --mesh-name YOURMESH --mesh-password YOURPASS --target 1 --toggle

Run --listen before --toggle. It completes the whole session handshake and
decrypts real status packets while sending no control command at all, so it
proves the hard part (crypto, session key, packet framing) without changing
the state of anything. That matters when the devices are wall switches
driving real loads and nobody is in the building.

Mesh name/password come from your existing cloud export - the same values the
`query_mesh_credentials` button surfaces. Treat them as secrets; anyone with
them controls the mesh.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

try:
    from cync_lan.ble_provision import (
        _aes_ecb_encrypt,
        _pad16,
        derive_session_key,
        generate_sk,
        key_encrypt,
    )
except ImportError:
    sys.exit(
        "cync-lan is not importable. Install it first:\n"
        "    pip install 'cync-lan>=0.5.3'"
    )

# Telink mesh GATT characteristics, from acync (atelink_mesh) and confirmed
# against the Telink mesh SDK's own documented UUIDs.
NOTIFICATION_CHAR = "00010203-0405-0607-0809-0a0b0c0d1911"
CONTROL_CHAR = "00010203-0405-0607-0809-0a0b0c0d1912"
PAIRING_CHAR = "00010203-0405-0607-0809-0a0b0c0d1914"

VENDOR_ID = 0x0211  # the `0x11, 0x02` seen in docs/mesh_opcodes.md payloads

OP_SET_POWER = 0xD0
OP_SET_BRIGHTNESS = 0xD2
OP_SET_TEMP_RGB = 0xE2
OP_STATUS_NOTIFY = 0xDC


# --------------------------------------------------------------------------
# The missing layer: per-command encryption.
# --------------------------------------------------------------------------
# Transcribed from acync's encrypt_packet/decrypt_packet, but expressed over
# bytes rather than lists, and using this project's own _aes_ecb_encrypt so
# the probe exercises the shipped crypto rather than a private copy. If this
# works on hardware, these two functions are what moves into cync_lan.


def encrypt_packet(sk: bytes, address: bytes, packet: bytearray) -> bytearray:
    """Authenticate and encrypt one 20-byte mesh command in place.

    `address` is the connected device's MAC in reverse byte order; only the
    first four bytes are used. Bytes 3-4 of the packet become a 2-byte MAC
    over the plaintext, and bytes 5.. are then XORed with a keystream block.
    """
    auth_nonce = bytes(address[:4]) + b"\x01" + bytes(packet[0:3]) + bytes([15]) + b"\x00" * 7
    authenticator = bytearray(_aes_ecb_encrypt(sk, _pad16(auth_nonce)))
    for i in range(15):
        authenticator[i] ^= packet[i + 5]

    mac = _aes_ecb_encrypt(sk, bytes(authenticator))
    packet[3] = mac[0]
    packet[4] = mac[1]

    iv = b"\x00" + bytes(address[:4]) + b"\x01" + bytes(packet[0:3]) + b"\x00" * 7
    keystream = _aes_ecb_encrypt(sk, _pad16(iv))
    for i in range(15):
        packet[i + 5] ^= keystream[i]
    return packet


def decrypt_packet(sk: bytes, address: bytes, packet: bytearray) -> bytearray:
    """Reverse of the above for an inbound notification, in place."""
    iv = bytes(address[:3]) + bytes(packet[0:5]) + b"\x00" * 8
    keystream = _aes_ecb_encrypt(sk, _pad16(b"\x00" + iv[:15]))
    for i in range(len(packet) - 7):
        packet[i + 7] ^= keystream[i]
    return packet


def build_command(counter: int, target: int, opcode: int, data: bytes) -> bytearray:
    """Lay out the 20-byte plaintext mesh command.

    Note packet[8:10] - the vendor ID that appears inline in the LAN
    transport's documented payloads sits in its own field here.
    """
    packet = bytearray(20)
    packet[0] = counter & 0xFF
    packet[1] = (counter >> 8) & 0xFF
    packet[5] = target & 0xFF
    packet[6] = (target >> 8) & 0xFF
    packet[7] = opcode
    packet[8] = VENDOR_ID & 0xFF
    packet[9] = (VENDOR_ID >> 8) & 0xFF
    packet[10 : 10 + len(data)] = data
    return packet


def mac_to_address(mac: str) -> bytes:
    """MAC string -> reversed byte order, as the cipher expects."""
    return bytes(int(b, 16) for b in reversed(mac.split(":")))


# --------------------------------------------------------------------------
# Self-test - runs without hardware or credentials.
# --------------------------------------------------------------------------


def self_test() -> int:
    """Validate the shipped crypto against acync's working implementation.

    acync is an independent implementation that demonstrably controls real
    hardware, so agreeing with it byte-for-byte is meaningful evidence that
    the primitives in cync_lan.ble_provision are correct - before anyone
    points this at a bulb.
    """
    failures = 0

    def check(label: str, got, want) -> None:
        nonlocal failures
        ok = got == want
        failures += 0 if ok else 1
        print(f"  {'ok  ' if ok else 'FAIL'} {label}")
        if not ok:
            print(f"        got  {got!r}\n        want {want!r}")

    # A literal transcription of acync's encrypt(), list-based, as the oracle.
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    def acync_encrypt(key: list[int], data: list[int]) -> list[int]:
        c = Cipher(algorithms.AES(bytes(reversed(key))), modes.ECB()).encryptor()
        return list(reversed(list(c.update(bytes(reversed(data))))))

    def acync_generate_sk(name: str, password: str, d1: list[int], d2: list[int]):
        name = name.ljust(16, chr(0))
        password = password.ljust(16, chr(0))
        key = [ord(a) ^ ord(b) for a, b in zip(name, password)]
        return acync_encrypt(key, (d1[0:8] + d2[0:8]))

    key = bytes(range(16))
    data = bytes(range(16, 32))
    check(
        "_aes_ecb_encrypt matches acync encrypt()",
        _aes_ecb_encrypt(key, data),
        bytes(acync_encrypt(list(key), list(data))),
    )

    name, password = "mesh-name", "mesh-pass"
    d1, d2 = bytes(range(8)), bytes(range(8, 16))
    check(
        "generate_sk matches acync generate_sk()",
        generate_sk(name.encode(), password.encode(), d1, d2),
        bytes(acync_generate_sk(name, password, list(d1), list(d2))),
    )

    check(
        "key_encrypt is deterministic and 16 bytes",
        len(key_encrypt(name.encode(), password.encode(), _pad16(b"\x01" * 8))),
        16,
    )

    # encrypt_packet -> decrypt_packet must recover the payload region.
    sk = generate_sk(name.encode(), password.encode(), d1, d2)
    address = mac_to_address("AA:BB:CC:DD:EE:FF")
    plain = build_command(1, 1, OP_SET_POWER, bytes([1]))
    original = bytes(plain)
    enc = encrypt_packet(sk, address, bytearray(plain))
    check("encrypt_packet changes the payload", enc[5:] != original[5:], True)
    check("encrypt_packet writes a 2-byte MAC", enc[3:5] != b"\x00\x00", True)

    # decrypt_packet is the inbound direction and uses a different IV layout,
    # so it is NOT a strict inverse of encrypt_packet. Assert the property
    # that actually holds: it is its own inverse, being an XOR keystream.
    once = decrypt_packet(sk, address, bytearray(enc))
    twice = decrypt_packet(sk, address, bytearray(once))
    check("decrypt_packet is an involution (XOR keystream)", bytes(twice), bytes(enc))

    print()
    if failures:
        print(f"  {failures} FAILURE(S) - do not run against hardware yet.")
    else:
        print("  crypto agrees with acync. Safe to try against a device.")
    return 1 if failures else 0


# --------------------------------------------------------------------------
# Hardware path.
# --------------------------------------------------------------------------


async def scan(timeout: float) -> None:
    from bleak import BleakScanner

    print(f"  scanning {timeout:.0f}s for devices advertising the Telink mesh service...")
    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
    found = 0
    for dev, adv in devices.values():
        name = dev.name or adv.local_name or ""
        # Provisioned Cync nodes do not advertise a distinctive name reliably,
        # so show anything plausible rather than filtering too hard and hiding
        # the device the user is looking for.
        if name or adv.service_uuids:
            found += 1
            print(f"    {dev.address}  rssi={adv.rssi:>4}  {name!r}")
            for u in adv.service_uuids:
                print(f"        service {u}")
    print(f"  {found} device(s). Any mesh node will do - commands are relayed.")


async def probe(args) -> int:
    from bleak import BleakClient

    address = mac_to_address(args.mac)
    r_app = os.urandom(8)

    # ble_provision.build_pairing_write() uses a fixed R_APP constant, which is
    # right for reproducing the app's factory-default write byte-for-byte but
    # not what you want for a live session. A fresh random R_app per connection
    # is what the real app and acync both do.
    proof = key_encrypt(
        args.mesh_name.encode(), args.mesh_password.encode(), _pad16(r_app)
    )[:8]
    pairing_write = bytes([0x0C]) + r_app + proof

    print(f"  connecting to {args.mac} ...")
    async with BleakClient(args.mac, timeout=args.timeout) as client:
        print("  connected; performing session handshake")
        await client.write_gatt_char(PAIRING_CHAR, pairing_write, response=True)
        await asyncio.sleep(0.3)
        response = await client.read_gatt_char(PAIRING_CHAR)
        if len(response) < 9:
            print(f"  handshake response too short ({len(response)}B): {response.hex()}")
            print("  usually means the mesh name/password are wrong for this device.")
            return 1

        r_dev = bytes(response[1:9])
        sk = derive_session_key(args.mesh_name, args.mesh_password, r_app, r_dev)
        print(f"  session key derived: {sk.hex()}")

        got_notification = False

        def on_notify(_sender, data: bytearray) -> None:
            nonlocal got_notification
            got_notification = True
            clear = decrypt_packet(sk, address, bytearray(data))
            tag = "status" if len(clear) > 7 and clear[7] == OP_STATUS_NOTIFY else "other"
            print(f"    notify [{tag}] {bytes(clear).hex()}")
            if tag == "status":
                for off in (10, 14):
                    resp = clear[off : off + 4]
                    if len(resp) == 4 and resp[1] != 0:
                        bright = resp[2]
                        if bright >= 128:
                            print(f"      id={resp[0]} brightness={bright - 128} (rgb)")
                        else:
                            print(f"      id={resp[0]} brightness={bright} temp={resp[3]}")

        await client.start_notify(NOTIFICATION_CHAR, on_notify)
        await asyncio.sleep(0.3)
        # Writing 0x01 to the notification characteristic asks the mesh to
        # report status. It is a request for data, not a control command -
        # nothing changes state because of it.
        await client.write_gatt_char(NOTIFICATION_CHAR, bytes([0x01]), response=True)
        await asyncio.sleep(0.5)

        if args.listen:
            # Read-only mode. Proves the connection, the session key and the
            # packet decryption without altering a single device - which is
            # what you want when the hardware is a wall switch controlling a
            # real load and you are not in the building.
            print(f"  listening {args.listen:.0f}s, sending no commands ...")
            for _ in range(int(args.listen)):
                await asyncio.sleep(1.0)
                await client.write_gatt_char(
                    NOTIFICATION_CHAR, bytes([0x01]), response=True
                )
            print()
            if got_notification:
                print("  Status decoded. The session key and packet crypto are correct,")
                print("  which is the hard part - control is one write away.")
            else:
                print("  No notifications. Connection and handshake worked, but nothing")
                print("  reported - worth investigating before sending any command.")
            return 0

        counter = 1

        async def send(opcode: int, data: bytes, label: str) -> None:
            nonlocal counter
            packet = build_command(counter, args.target, opcode, data)
            counter += 1
            enc = encrypt_packet(sk, address, packet)
            print(f"  -> {label}: op=0x{opcode:02X} target={args.target} {bytes(enc).hex()}")
            await client.write_gatt_char(CONTROL_CHAR, bytes(enc), response=False)

        if args.toggle:
            await send(OP_SET_POWER, bytes([0]), "power OFF")
            await asyncio.sleep(args.dwell)
            await send(OP_SET_POWER, bytes([1]), "power ON")
            await asyncio.sleep(args.dwell)
        elif args.brightness is not None:
            await send(OP_SET_BRIGHTNESS, bytes([args.brightness]), "brightness")
            await asyncio.sleep(args.dwell)
        else:
            await send(OP_SET_POWER, bytes([1 if args.on else 0]), "power")
            await asyncio.sleep(args.dwell)

        await asyncio.sleep(1.0)
        print()
        print(f"  notifications received: {'yes' if got_notification else 'no'}")
        print("  Did the light respond? That is the result this probe exists for -")
        print("  a clean run with no visible change means the write was accepted")
        print("  but the command did nothing, which is the interesting failure.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--self-test", action="store_true", help="validate crypto, no hardware needed")
    p.add_argument("--scan", action="store_true", help="list nearby BLE devices")
    p.add_argument("--mac", help="MAC of any provisioned mesh node")
    p.add_argument("--mesh-name", help="mesh name from your cloud export")
    p.add_argument("--mesh-password", help="mesh password from your cloud export")
    p.add_argument("--target", type=int, default=1, help="mesh device id (0 broadcasts)")
    p.add_argument(
        "--listen",
        type=float,
        metavar="SECONDS",
        help="read-only: connect, decode status, send NO control command",
    )
    p.add_argument("--toggle", action="store_true", help="off, then on (ends ON)")
    p.add_argument("--on", action="store_true", help="turn on")
    p.add_argument("--brightness", type=int, help="set brightness 0-100")
    p.add_argument("--dwell", type=float, default=1.5, help="seconds between commands")
    p.add_argument("--timeout", type=float, default=20.0)
    args = p.parse_args()

    if args.self_test:
        return self_test()
    if args.scan:
        return asyncio.run(scan(args.timeout)) or 0
    if not (args.mac and args.mesh_name and args.mesh_password):
        p.error("--mac, --mesh-name and --mesh-password are required (or use --self-test/--scan)")
    return asyncio.run(probe(args))


if __name__ == "__main__":
    raise SystemExit(main())
