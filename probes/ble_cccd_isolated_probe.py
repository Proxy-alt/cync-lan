"""Isolated CCCD-write test: is the earlier rejection about the CCCD write
ITSELF, or specific to BlueZ's/bumble's subscribe() sequence?

Motivated by a real gap in the earlier finding: every avoidance path tested
(Android, bluepy, dimond, bumble's own no-CCCD registration) shares the trait
of never writing the CCCD at all. Nothing isolated a manually-sent, standard,
spec-compliant CCCD write performed OUTSIDE of Client.subscribe() (which we
confirmed DOES write the CCCD, the same way BlueZ's StartNotify does - and
gets rejected).

CoreBluetooth on iOS is fully spec-compliant and offers no way to skip the
CCCD write at all - Cync ships an iOS app - so if that app gets live status,
this firmware must accept a standards-compliant subscription from SOME
sequence. This test sends exactly that value (struct.pack('<H', 1), the
standard notify-enable bit) directly to the CCCD handle via write_value(),
bypassing subscribe() entirely, to see whether it is accepted.

Also captures the exact ATT error code on rejection (via bumble.att.ATT_Error),
not just bleak's translated exception name - 0x0E "Unlikely Error" (what
BlueZ/bumble's subscribe() produced) is NOT one of the standard
security-related codes (0x05 Insufficient Authentication, 0x0F Insufficient
Encryption, 0x08 Insufficient Authorization) a bonding requirement would
produce, which argues against "needs real BLE bonding" as the explanation.

Confirmed from bumble's own source before this script was written:
- gatt_client.Client.subscribe() DOES write the CCCD (same as bleak/BlueZ) -
  so it is never called here.
- gatt_client.Client.on_att_handle_value_notification() fires for EVERY raw
  ATT notification PDU that arrives on the connection, unconditionally - it
  only consults notification_subscribers afterward to decide who to call, and
  merely logs a warning (not a rejection) if nobody is registered.
- Because bumble talks raw HCI directly, WE are the ATT client. There is no
  intermediary daemon (unlike BlueZ) enforcing "you must subscribe via CCCD
  before I deliver PDUs to you" - that policy lives in BlueZ's D-Bus GATT
  layer, not in the ATT protocol itself.

So: populate client.notification_subscribers[handle] directly, skip
subscribe() and therefore skip the CCCD write entirely, and see if this
firmware's notifications simply arrive - matching what Android's app and
bluepy already do for the same underlying reason.

DELIBERATELY NARROW SCOPE for this first raw-HCI run: notifications only, no
control write. This adapter (hci0) is shared with real Home Assistant
integrations (yalexs_ble, ibeacon, core bluetooth) via BlueZ, which this
script takes over exclusively for its duration via HCI_CHANNEL_USER. Kept as
short and single-purpose as possible.
"""
import asyncio
import os
import sys
import traceback
import yaml

from cync_lan.ble_mesh import decrypt_packet, mac_to_address, parse_status
from cync_lan.ble_provision import (
    _pad16,
    derive_session_key,
    key_encrypt,
    verify_pairing_response,
)

import bumble.att as batt
import bumble.core as bcore
import bumble.device as bdevice
import bumble.hci as hci
import bumble.transport as btransport

SERVICE_UUID = "00010203-0405-0607-0809-0a0b0c0d1910"
PAIRING_UUID = "00010203-0405-0607-0809-0a0b0c0d1914"
NOTIFY_UUID = "00010203-0405-0607-0809-0a0b0c0d1911"
CONTROL_UUID = "00010203-0405-0607-0809-0a0b0c0d1912"


def find_home(cfg, want_mesh_name):
    homes = []

    def walk(n):
        if isinstance(n, dict):
            if "access_key" in n and "mac" in n:
                homes.append(n)
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(cfg)
    match = [h for h in homes if str(h["mac"]) == want_mesh_name]
    if not match:
        sys.exit(f"mesh {want_mesh_name} not found among {[h['mac'] for h in homes]}")
    return match[0]


async def run(config_path: str, mac: str, mesh_name: str, listen_seconds: float):
    home = find_home(yaml.safe_load(open(config_path)), mesh_name)
    name, pw = str(home["mac"]), str(home["access_key"])
    print(f"  mesh {name} (password not shown)")

    # ---- Everything above this line needed no exclusive hardware access. ----
    # ---- The next line takes hci0 away from BlueZ via HCI_CHANNEL_USER.  ----
    print("  opening hci-socket:0 (this takes hci0 away from BlueZ)...")
    transport = await btransport.open_transport("hci-socket:0")
    print("  hci-socket opened; BlueZ no longer manages hci0 until this closes")

    device = bdevice.Device.with_hci(
        "bumble-probe",
        hci.Address("00:00:00:00:00:00"),  # placeholder; power_on reads the real one
        transport.source,
        transport.sink,
    )

    try:
        await asyncio.wait_for(device.power_on(), timeout=10)
        print(f"  controller reset ok, public address: {device.public_address}")

        # Bypassing Peer deliberately: Peer.__aenter__() runs an UNFILTERED
        # discover_services() over every service on the device (this one has
        # four - GAP, GATT, Device Info, and the Telink vendor service), which
        # is very plausibly why the previous attempt's discovery hung past a
        # 15s timeout. Going straight to connection.gatt_client and scoping
        # discovery to only the one service UUID we actually want.
        connection = await asyncio.wait_for(
            device.connect(f"{mac}/P"), timeout=15
        )
        print("  connected (raw connection, no Peer wrapper)")
        link_down = {"yes": False}
        connection.on(
            connection.EVENT_DISCONNECTION, lambda reason: link_down.__setitem__("yes", True)
        )
        client = connection.gatt_client
        try:
            services = await asyncio.wait_for(
                client.discover_services([bcore.UUID(SERVICE_UUID)]), timeout=15
            )
            print(f"  discovered {len(services)} matching service(s)")
            if not services:
                print("  !!! Telink service not found on this device")
                return
            chars = await asyncio.wait_for(
                client.discover_characteristics([], services[0]), timeout=15
            )
            print(f"  discovered {len(chars)} characteristics in that service")
            by_uuid = {str(c.uuid).lower(): c for c in chars}
            pairing_char = by_uuid.get(f"0x{PAIRING_UUID}".lower()) or by_uuid.get(
                PAIRING_UUID.lower()
            )
            notify_char = by_uuid.get(f"0x{NOTIFY_UUID}".lower()) or by_uuid.get(
                NOTIFY_UUID.lower()
            )
            if pairing_char is None or notify_char is None:
                print("  !!! could not match characteristics by UUID; found:")
                for u, c in by_uuid.items():
                    print(f"      {u}  handle=0x{c.handle:04X}")
                return
        except Exception:
            print("  !!! discovery failed, full traceback:")
            traceback.print_exc()
            return
        try:
            print(
                f"  found pairing char handle=0x{pairing_char.handle:04X}, "
                f"notify char handle=0x{notify_char.handle:04X}"
            )

            r_app = os.urandom(8)
            proof = key_encrypt(name.encode(), pw.encode(), _pad16(r_app))[:8]
            await client.write_value(
                pairing_char, bytes([0x0C]) + r_app + proof, with_response=True
            )
            await asyncio.sleep(0.3)
            resp = bytes(await client.read_value(pairing_char))
            sk = derive_session_key(name, pw, r_app, resp[1:9])
            verified = verify_pairing_response(name, pw, resp)
            print(f"  mutual auth: {'VERIFIED' if verified else 'FAILED'}")
            if not verified:
                print("  aborting - not a meaningful test with unverified credentials")
                return

            # discover_descriptors(notify_char) returned nothing on the first
            # attempt at this test - notify_char.end_group_handle was evidently
            # not what this code assumed, and that machinery is not itself what
            # tonight's test is about. Using the CCCD handle directly instead:
            # bleak's own --gatt dump of this exact device, earlier tonight,
            # independently confirmed the CCCD at handle 19 (0x13), immediately
            # following the notify characteristic's value handle - which bumble
            # has *just* confirmed is 0x12 on this run, matching that dump
            # exactly. Two independent tools agreeing on the value handle is
            # good cross-confirmation for the descriptor handle that follows it.
            cccd_handle = notify_char.handle + 1
            print(
                f"  CCCD handle=0x{cccd_handle:04X} (from the earlier independent "
                f"bleak dump, not re-discovered this run)"
            )

            # The isolated test: the exact standard notify-enable value, sent as
            # a plain ATT write to the CCCD handle - the same value
            # Client.subscribe()/BlueZ/iOS would all write - but via write_value()
            # directly, never touching subscribe() at all.
            import struct

            cccd_write_ok = False
            try:
                await client.write_value(
                    cccd_handle, struct.pack("<H", 1), with_response=True
                )
                cccd_write_ok = True
                print("  CCCD write: ACCEPTED (bypassing subscribe())")
            except batt.ATT_Error as exc:
                print(
                    f"  CCCD write: REJECTED, ATT error code 0x{exc.error_code:02X} "
                    f"({exc.error_code})"
                )
            except Exception as exc:
                print(f"  CCCD write: failed with {type(exc).__name__}: {exc}")

            print(f'  link alive after CCCD write attempt: {not link_down["yes"]}')

            # The load-bearing step: register directly, never call subscribe().
            received = []

            addr = mac_to_address(mac)

            def on_notify(value: bytes) -> None:
                raw = bytes(value)
                received.append(raw)
                clear = decrypt_packet(sk, addr, bytearray(raw))
                statuses = parse_status(bytes(clear))
                tag = f"opcode=0x{clear[7]:02X}" if len(clear) > 7 else "short"
                print(f"    notify [{tag}] decrypted={bytes(clear).hex()}")
                for st in statuses:
                    print(f"        id={st.device_id} brightness={st.brightness} temp={st.colour_temp}")

            client.notification_subscribers.setdefault(notify_char.handle, set())
            client.notification_subscribers[notify_char.handle].add(on_notify)
            if cccd_write_ok:
                print("  registered local notification callback (CCCD write DID succeed above)")
            else:
                print("  registered local notification callback (CCCD write was rejected above)")

            # The vendor's own "start reporting" command - a plain characteristic
            # value write, not a descriptor write.
            await client.write_value(notify_char, bytes([0x01]), with_response=True)
            print(f"  enable-write sent; listening {listen_seconds:.0f}s...")

            for _ in range(int(listen_seconds)):
                await asyncio.sleep(1.0)
                if link_down["yes"]:
                    print("  *** link dropped during listen ***")
                    break

            print(f'  still connected: {not link_down["yes"]}')
            print(f"  notifications received: {len(received)}")
            print()
            print(
                f"  SUMMARY: manual CCCD write "
                f"{'ACCEPTED' if cccd_write_ok else 'REJECTED'}, "
                f"link {'survived' if not link_down['yes'] else 'dropped'} the full window, "
                f"{len(received)} notifications decrypted"
            )
        finally:
            if not link_down["yes"]:
                try:
                    await connection.disconnect()
                except Exception:
                    pass
    finally:
        print("  closing transport, handing hci0 back to BlueZ...")
        await device.power_off()
        await transport.close()
        print("  transport closed")


async def main():
    config_path, mac, mesh_name = sys.argv[1], sys.argv[2], sys.argv[3]
    listen_seconds = float(sys.argv[4]) if len(sys.argv) > 4 else 10.0
    # Hard backstop: even if something above hangs, this guarantees the
    # process (and therefore the HCI_CHANNEL_USER socket) is torn down and
    # hci0 is released back to BlueZ.
    await asyncio.wait_for(run(config_path, mac, mesh_name, listen_seconds), timeout=60)


asyncio.run(main())
