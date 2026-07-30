"""Closed-loop test: does harvested mesh state report what we just set?

`parse_status`'s slot layout is the one piece of this protocol marked
"plausible, not confirmed" - decoded from a single capture, and its presence
rule contradicts acync's. Every harvest so far decoded 34-38 device ids with
brightness 0 across the board, which is consistent with both "the decode is
right and the house was dark" and "the decode is wrong and always says zero".

This separates them. Drive one device to a known, distinctive brightness over
BLE, harvest, and see whether that device - and only that device - comes back
at the level it was set to. Matching a specific value is hard to do by
accident; matching several in sequence is harder still.

Commands and harvests need separate connections: subscribing is what kills
the link (confirmed, ~30s, every time), so a session that harvests cannot
then be used to send.

Usage:
    state_decode_test.py <config.yaml> <mesh_name> <target_mac> <device_id>
"""

from __future__ import annotations

import asyncio
import sys

import yaml
from bleak import BleakClient
from cync_lan.ble_mesh import (
    NOTIFICATION_CHAR,
    BleMeshSession,
    decrypt_packet,
    mac_to_address,
    parse_status,
)

HARVEST_SECONDS = 22.0
SETTLE_AFTER_COMMAND = 3.0


def load_credentials(path: str, mesh_name: str) -> tuple[str, str]:
    homes: list[dict] = []

    def walk(node):
        if isinstance(node, dict):
            if "access_key" in node and "mac" in node:
                homes.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(yaml.safe_load(open(path)))
    for home in homes:
        if str(home["mac"]) == mesh_name:
            return str(home["mac"]), str(home["access_key"])
    sys.exit(f"mesh {mesh_name} not found in {path}")


async def _session(mac: str, name: str, password: str):
    client = BleakClient(mac)
    await asyncio.wait_for(client.connect(), timeout=25)
    session = BleMeshSession(client, mac, name, password)
    if not await session.authenticate():
        await client.disconnect()
        raise SystemExit("mutual auth rejected - wrong mesh credentials")
    return client, session


async def send(mac: str, name: str, password: str, action) -> None:
    """One command connection. Never subscribes, so it stays healthy."""
    client, session = await _session(mac, name, password)
    try:
        await action(session)
        # Fire-and-forget transport: nothing acknowledges, so give the mesh a
        # moment to relay before tearing the link down under it.
        await asyncio.sleep(SETTLE_AFTER_COMMAND)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def harvest(mac: str, name: str, password: str) -> dict[int, int]:
    """One sacrificial connection: subscribe, collect the sweep, lose the link."""
    client, session = await _session(mac, name, password)
    states: dict[int, int] = {}
    address = mac_to_address(mac)
    key = session._session_key

    def on_notify(_sender, data: bytearray) -> None:
        try:
            clear = decrypt_packet(key, address, bytearray(data))
            for status in parse_status(bytes(clear)):
                states[status.device_id] = status.brightness
        except Exception:
            pass

    try:
        await client.write_gatt_char(NOTIFICATION_CHAR, bytes([0x01]), response=True)
        try:
            await client.start_notify(NOTIFICATION_CHAR, on_notify)
        except Exception:
            # Expected - the CCCD write is refused. The callback is already
            # registered locally by then, which is the whole trick.
            pass
        await asyncio.sleep(HARVEST_SECONDS)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
    return states


async def main() -> None:
    config, mesh_name, mac, target = (
        sys.argv[1],
        sys.argv[2],
        sys.argv[3],
        int(sys.argv[4]),
    )
    name, password = load_credentials(config, mesh_name)
    print(f"mesh {name} via {mac}, driving device id {target}\n")

    async def step(label: str, action=None) -> dict[int, int]:
        if action is not None:
            await send(mac, name, password, action)
        states = await harvest(mac, name, password)
        reported = states.get(target)
        print(
            f"{label:<26} device {target}: "
            f"{'(not reported)' if reported is None else reported:<14} "
            f"({len(states)} ids decoded)"
        )
        return states

    baseline = await step("baseline")

    await step("after ON + brightness 60", lambda s: _on_at(s, target, 60))
    await step("after brightness 25", lambda s: s.set_brightness(target, 25))
    await step("after OFF", lambda s: s.set_power(target, False))

    print()
    print("Restoring: leaving the device off, which is where it started"
          if not baseline.get(target) else
          f"NOTE: device was at {baseline[target]} before this run, now off")


async def _on_at(session: BleMeshSession, target: int, level: int) -> None:
    await session.set_power(target, True)
    await asyncio.sleep(1.0)
    await session.set_brightness(target, level)


asyncio.run(main())
