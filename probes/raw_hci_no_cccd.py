"""Contrast capture: notifications with no CCCD write, over raw HCI.

The BlueZ report needs a control. Everything on BlueZ goes through
`StartNotify`, which unconditionally writes the assumed CCCD, so BlueZ cannot
demonstrate the alternative even in principle. bumble binds `HCI_CHANNEL_USER`
and becomes the ATT client itself, which makes the descriptor irrelevant:
notification PDUs are delivered to whoever is listening on the connection.

The point is to show the same device, the same handles, the same session, with
the one write removed - and have it work. If it does, the write is not what
enables notifications, and losing the connection over it is pure cost.

**This takes hci0 away from BlueZ for its duration.** The adapter must be down
before `HCI_CHANNEL_USER` can be bound, so Home Assistant's Bluetooth stops for
as long as this runs. The caller is responsible for bringing it back.
"""

from __future__ import annotations

import asyncio
import json
import sys

from bumble.device import Device
from bumble.hci import Address
from bumble.transport import open_transport

NOTIFY_VALUE_HANDLE = 0x0012
PAIRING_HANDLE = 0x001B
LISTEN_SECONDS = 15.0


def entry() -> dict:
    with open("/config/.storage/core.config_entries") as handle:
        for item in json.load(handle)["data"]["entries"]:
            if item["domain"] == "cync_ble":
                return item
    raise SystemExit("no cync_ble config entry")


async def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else None
    if not target:
        raise SystemExit("usage: raw_hci_no_cccd.py <MAC>")

    conf = entry()
    mesh_name = conf["data"]["mesh_name"]
    mesh_password = conf["data"]["mesh_password"]

    from bumble.device import Peer

    from cync_lan.ble_mesh import decrypt_packet, mac_to_address, parse_status
    from cync_lan.ble_provision import R_APP, build_pairing_write, derive_session_key

    async with await open_transport("hci-socket:0") as transport:
        device = Device.with_hci(
            "probe", Address("F0:F1:F2:F3:F4:F5"), transport.source, transport.sink
        )
        await device.power_on()
        print(f"[*] connecting to {target} over raw HCI - BlueZ is not involved")
        connection = await device.connect(Address(f"{target}/P"))
        print("[+] connected")

        peer = Peer(connection)
        await peer.discover_services()
        for service in peer.services:
            await peer.discover_characteristics(service=service)
        gatt = peer.gatt_client

        await gatt.write_value(
            PAIRING_HANDLE, build_pairing_write(mesh_name, mesh_password),
            with_response=True,
        )
        response = bytes(await gatt.read_value(PAIRING_HANDLE))
        session_key = derive_session_key(
            mesh_name, mesh_password, R_APP, response[1:9]
        )
        print("[+] paired, session key derived")

        address = mac_to_address(target)
        received: list[bytes] = []
        dropped = False

        def _on_disconnect(_reason=None):
            nonlocal dropped
            dropped = True

        connection.on("disconnection", _on_disconnect)
        # THE POINT: register the subscriber directly. bumble's own subscribe()
        # writes the CCCD exactly as BlueZ does, so it is never called. As our
        # own ATT client we simply listen for the PDUs.
        gatt.notification_subscribers[NOTIFY_VALUE_HANDLE] = {
            lambda value: received.append(bytes(value))
        }
        print("[*] NO CCCD WRITE PERFORMED - subscriber registered locally")

        print("[*] vendor enable-write to 0x0012")
        await gatt.write_value(
            NOTIFY_VALUE_HANDLE, bytes([0x01]), with_response=True
        )

        print(f"[*] listening {LISTEN_SECONDS:.0f}s...")
        await asyncio.sleep(LISTEN_SECONDS)

        seen: set[int] = set()
        for packet in received:
            try:
                clear = bytes(decrypt_packet(session_key, address, bytearray(packet)))
                for status in parse_status(clear) or ():
                    seen.add(status.device_id)
            except Exception:
                pass

        print("")
        print("=" * 58)
        print(f"notifications received : {len(received)}")
        print(f"distinct devices seen  : {len(seen)}")
        print(f"CCCD writes performed  : 0")
        print(f"connection still up    : {not dropped}")
        print("=" * 58)
        await connection.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
