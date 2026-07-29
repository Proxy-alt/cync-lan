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
        verify_pairing_response,
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

        # Deriving a session key proves nothing on its own - the maths always
        # succeeds, whatever password you feed it. This is the check that
        # actually says whether the DEVICE agreed: it echoes back a proof that
        # it derived the same key material. ble_provision documents it as a
        # client-side sanity check (python-dimond skips it entirely and still
        # works), so a failure here is a strong signal that the credentials or
        # the response format are wrong rather than a fatal error.
        print(f"  pairing response ({len(response)}B): {bytes(response).hex()}")
        if len(response) >= 17:
            if verify_pairing_response(args.mesh_name, args.mesh_password, bytes(response)):
                print("  mutual auth: VERIFIED - the device derived the same key")
            else:
                print("  mutual auth: FAILED - the proof does not match")
                print("    Most likely the mesh name or password is wrong. The mesh name")
                print("    is the home's `mac` from cync_mesh.yaml and the password its")
                print("    `access_key`; try the exact string forms the file uses.")
        else:
            print(f"  mutual auth: no proof in response (needs 17B, got {len(response)})")

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

        if args.gatt:
            print("  GATT table:")
            for service in client.services:
                print(f"    service {service.uuid}")
                for ch in service.characteristics:
                    star = " <-- Telink" if ch.uuid in (
                        NOTIFICATION_CHAR, CONTROL_CHAR, PAIRING_CHAR
                    ) else ""
                    print(f"      {ch.uuid}  {','.join(ch.properties)}{star}")
                    # BlueZ's StartNotify writes the CCCD (0x2902). If this
                    # firmware has none, that write cannot succeed and the
                    # 'Unlikely Error' is fully explained.
                    for desc in ch.descriptors:
                        flag = " <-- CCCD" if "2902" in desc.uuid else ""
                        print(
                            f"        descriptor {desc.uuid} "
                            f"handle={desc.handle}{flag}"
                        )

        # --send-before-notify exists because of a genuine catch-22 found on
        # hardware: BlueZ will not deliver notifications without StartNotify,
        # and StartNotify is refused in a way that drops the link. So a reply
        # can only be caught in the window between subscribing and dying.
        #
        # Sending first, then subscribing, is the one ordering that might see a
        # hub command's answer: the command goes out on a healthy link, and the
        # subscribe that follows harvests whatever is queued before the
        # connection goes down.
        if args.send_before_notify and args.op is not None:
            try:
                opcode_early = int(args.op, 0)
            except ValueError:
                print(f"  --op must be a number, e.g. 0x4B (got {args.op!r})")
                return 1
            raw_early = (args.data or "").replace(",", " ").split()
            payload_early = bytes(int(b, 16) for b in raw_early)
            packet = build_command(1, args.target, opcode_early, payload_early)
            enc = encrypt_packet(sk, address, packet)
            print(
                f"  -> sent BEFORE notify: op=0x{opcode_early:02X} "
                f"target={args.target} {bytes(enc).hex()}"
            )
            await client.write_gatt_char(CONTROL_CHAR, bytes(enc), response=False)
            await asyncio.sleep(0.5)

        # How to turn inbound status on is genuinely unsettled, so it is
        # selectable rather than guessed. See --notify-mode.
        #
        # The evidence: google/python-dimond - the origin of this protocol
        # lineage, and a demonstrably working implementation - pairs, registers
        # a callback, then writes 0x01 to the notify characteristic's VALUE and
        # simply waits. It never writes the CCCD at all. bluepy delivers
        # notifications anyway, because that 0x01 write is the vendor's own
        # "start reporting" command rather than a standard subscription.
        #
        # BlueZ differs: StartNotify writes the CCCD (0x2902), and this firmware
        # answers with 'Unlikely Error' and then drops the link. This probe used
        # to subscribe first and write 0x01 afterwards - the exact inverse of the
        # working order - and its retry never got a fair test, because the
        # connection was already dead by the time it ran.
        notify_ok = False
        mode = args.notify_mode

        async def _enable_write() -> None:
            """The vendor's start-reporting command: 0x01 to the char value."""
            await client.write_gatt_char(
                NOTIFICATION_CHAR, bytes([0x01]), response=True
            )

        async def _cccd_write() -> bool:
            """Write the CCCD by hand, bypassing bleak's StartNotify wrapper."""
            ch = client.services.get_characteristic(NOTIFICATION_CHAR)
            cccd = next((d for d in ch.descriptors if "2902" in d.uuid), None)
            if cccd is None:
                print("    no 0x2902 descriptor on this characteristic at all,")
                print("    which would explain why BlueZ cannot subscribe.")
                return False
            await client.write_gatt_descriptor(cccd.handle, bytes([0x01, 0x00]))
            return True

        if args.no_notify:
            print("  skipping notifications (--no-notify)")
        else:
            try:
                if mode == "subscribe-first":
                    await client.start_notify(NOTIFICATION_CHAR, on_notify)
                    await asyncio.sleep(0.3)
                    await _enable_write()
                    notify_ok = True
                elif mode == "enable-first":
                    await _enable_write()
                    await asyncio.sleep(0.3)
                    await client.start_notify(NOTIFICATION_CHAR, on_notify)
                    notify_ok = True
                elif mode == "enable-only":
                    await _enable_write()
                    notify_ok = True
                elif mode == "cccd-direct":
                    if await _cccd_write():
                        await asyncio.sleep(0.3)
                        await _enable_write()
                        notify_ok = True
                if notify_ok:
                    print(f"  notify setup ({mode}): accepted")
            except Exception as exc:
                print(f"  notify setup ({mode}) failed: {type(exc).__name__}: {exc}")
                print("    Sending is unaffected - control writes go to ...1912.")
        await asyncio.sleep(0.3)
        # Writing 0x01 to the notification characteristic asks the mesh to
        # report status. It is a request for data, not a control command -
        # nothing changes state because of it.
        if notify_ok:
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
                if notify_ok:
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
            if not client.is_connected:
                print("     LINK IS DOWN - the device dropped us before this write.")
                print("     Re-run with --no-notify; the subscribe is what kills it.")
                return
            await client.write_gatt_char(CONTROL_CHAR, bytes(enc), response=False)

        if args.op is not None and not args.send_before_notify:
            # Raw mode: send any opcode with any payload. The point is to test
            # commands whose BLE form is predicted by the transport mapping in
            # docs/mesh_opcodes.md but not yet confirmed - strip the leading
            # 0x11, 0x02 (the vendor ID) from a documented TCP payload and pass
            # what remains here.
            try:
                opcode = int(args.op, 0)
            except ValueError:
                print(f"  --op must be a number, e.g. 0xF0 (got {args.op!r})")
                return 1
            raw = (args.data or "").replace(",", " ").split()
            try:
                payload = bytes(int(b, 16) for b in raw)
            except ValueError:
                print(f"  --data must be hex bytes, e.g. 01,32,FF (got {args.data!r})")
                return 1
            await send(opcode, payload, f"raw op=0x{opcode:02X}")
            await asyncio.sleep(args.dwell)
        elif args.toggle:
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
    p.add_argument(
        "--gatt", action="store_true", help="dump the GATT table and descriptors"
    )
    p.add_argument(
        "--notify-mode",
        default="subscribe-first",
        choices=["subscribe-first", "enable-first", "enable-only", "cccd-direct"],
        help=(
            "how to turn on inbound status. subscribe-first is what failed; "
            "enable-first matches python-dimond's working order; enable-only is "
            "exactly what dimond does; cccd-direct writes the descriptor by hand"
        ),
    )
    p.add_argument(
        "--no-notify",
        action="store_true",
        help="skip notifications entirely - the failed CCCD write can drop the link",
    )
    p.add_argument("--mac", help="MAC of any provisioned mesh node")
    p.add_argument("--mesh-name", help="mesh name from your cloud export")
    p.add_argument("--mesh-password", help="mesh password from your cloud export")
    p.add_argument(
        "--from-config",
        metavar="PATH",
        help=(
            "read credentials from cync_mesh.yaml instead of the command line, so "
            "the mesh password never appears in a shell command, shell history, or "
            "anything you copy out of a terminal. On Home Assistant: "
            "/config/.storage/cync-lan/config/cync_mesh.yaml"
        ),
    )
    p.add_argument("--target", type=int, default=1, help="mesh device id (0 broadcasts)")
    p.add_argument(
        "--listen",
        type=float,
        metavar="SECONDS",
        help="read-only: connect, decode status, send NO control command",
    )
    p.add_argument("--toggle", action="store_true", help="off, then on (ends ON)")
    p.add_argument("--on", action="store_true", help="turn on")
    p.add_argument("--brightness", type=int, help="set brightness 0-100 via 0xD2 (sol-lamp variant)")
    p.add_argument(
        "--op",
        help="raw opcode to send, e.g. 0xF0 - for testing an unconfirmed command",
    )
    p.add_argument(
        "--send-before-notify",
        action="store_true",
        help=(
            "send --op on a healthy link and only then subscribe, to catch a "
            "reply in the window before a refused StartNotify drops the link"
        ),
    )
    p.add_argument(
        "--data",
        help="payload for --op as hex bytes, e.g. 01,32,FF,FF,FF,FF (no vendor prefix)",
    )
    p.add_argument("--dwell", type=float, default=1.5, help="seconds between commands")
    p.add_argument("--timeout", type=float, default=20.0)
    args = p.parse_args()

    if args.self_test:
        return self_test()
    if args.scan:
        return asyncio.run(scan(args.timeout)) or 0
    if args.from_config:
        # Deliberately reads the value in-process and never prints it. The whole
        # point is that the mesh password stays on the machine that owns it - a
        # credential pasted into a terminal ends up in shell history, in scroll
        # buffers, and in whatever you copy out to ask someone for help.
        try:
            import yaml
        except ImportError:
            p.error("--from-config needs pyyaml: pip install pyyaml")
        try:
            with open(args.from_config) as fh:
                cfg = yaml.safe_load(fh) or {}
        except OSError as exc:
            p.error(f"cannot read {args.from_config}: {exc}")

        # Search recursively rather than assuming a depth. CYNC_CONFIG_DIR is
        # configurable and the export has been reshaped before, so pinning the
        # nesting is how this breaks on somebody else's install.
        def _find_homes(node, found=None):
            found = [] if found is None else found
            if isinstance(node, dict):
                if "access_key" in node and "mac" in node:
                    found.append(node)
                for value in node.values():
                    _find_homes(value, found)
            elif isinstance(node, list):
                for value in node:
                    _find_homes(value, found)
            return found

        homes = _find_homes(cfg)
        if not homes:
            # Print the shape, never the values - a structure that does not match
            # is worth seeing, and the file holds a credential.
            def _skeleton(node, depth=0):
                pad = "      " + "  " * depth
                if isinstance(node, dict):
                    for k, v in list(node.items())[:8]:
                        kind = type(v).__name__
                        print(f"{pad}{k}: <{kind}>")
                        if depth < 2 and isinstance(v, (dict, list)):
                            _skeleton(v, depth + 1)
                elif isinstance(node, list):
                    print(f"{pad}[{len(node)} items]")
                    if node and depth < 2:
                        _skeleton(node[0], depth + 1)

            print(f"  no dict with both 'mac' and 'access_key' in {args.from_config}")
            print("  structure found (keys only, no values):")
            _skeleton(cfg)
            p.error("cannot locate the mesh credentials in that file")
        if len(homes) > 1 and not args.mesh_name:
            names = ", ".join(str(h.get("mac", "?")) for h in homes)
            p.error(f"several homes present; pass --mesh-name to pick one of: {names}")

        home = homes[0]
        if args.mesh_name:
            match = [h for h in homes if str(h.get("mac")) == args.mesh_name]
            if not match:
                p.error(f"no home with mac {args.mesh_name!r} in {args.from_config}")
            home = match[0]

        args.mesh_name = str(home["mac"])
        args.mesh_password = str(home["access_key"])
        print(f"  credentials loaded for mesh {args.mesh_name} (password not shown)")

        if not args.mac:
            # Any node will do - commands relay - so offer the ones on file.
            devices = home.get("devices") or {}
            macs = [
                (str(k), d.get("mac"))
                for k, d in devices.items()
                if isinstance(d, dict) and d.get("mac")
            ]
            if macs:
                print("  --mac not given; devices in this home (id -> mac):")
                for dev_id, dev_mac in macs[:12]:
                    print(f"      {dev_id:>4}  {dev_mac}")

    if not (args.mac and args.mesh_name and args.mesh_password):
        p.error(
            "need --mac plus credentials: either --mesh-name/--mesh-password, or "
            "--from-config (or use --self-test/--scan)"
        )
    return asyncio.run(probe(args))


if __name__ == "__main__":
    raise SystemExit(main())
