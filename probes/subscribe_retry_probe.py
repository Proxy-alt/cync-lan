"""Does the Telink notify subscription ever survive, if you keep retrying?

The real Cync iOS app ships `subscribeRetryCounter` / `subscriptionRetryTimer`
around exactly this call (confirmed by static analysis of CbyGEKit.framework),
which is only worth building if the call sometimes fails and sometimes works.
cync_ble currently gives up after one attempt per 30 minutes, on the evidence
of a single observation: subscribe() returned, and the link died 2s later.

One observation is not a rate. This retries hard and records the distribution,
so "push is impossible here" is either demonstrated or disproved with numbers.

For each attempt: connect, authenticate, send the vendor enable-write, call
start_notify, then watch. Records how long the link survived, and how many
status notifications arrived before it went. A run where any attempt holds the
link for the full observation window - and keeps delivering notifications -
would mean push is viable with retry, and cync_ble's backoff is wrong.

Usage:
    subscribe_retry_probe.py <config.yaml> <mesh_name> <attempts> [watch_secs]
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time

import yaml
from bleak import BleakClient, BleakScanner
from cync_lan.ble_mesh import (
    NOTIFICATION_CHAR,
    BleMeshSession,
    decrypt_packet,
    mac_to_address,
    parse_status,
)


def load_credentials(path: str, mesh_name: str) -> tuple[str, str]:
    """Mesh name/password out of cync-lan's exported config.

    Matches the requested mesh explicitly rather than taking the first
    home found - this account has more than one, and the other fails
    mutual auth, which is a confusing way to learn you probed the wrong
    mesh.
    """
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
    sys.exit(f"mesh {mesh_name} not in {path} (found {[h['mac'] for h in homes]})")


async def pick_target(mesh_name: str) -> str:
    """Any advertising node on this mesh will do - relay means one
    connection reaches all of them."""
    print("scanning for a reachable mesh node...")
    found = await BleakScanner.discover(timeout=10.0)
    ouis = ("F4:BC:DA", "30:C0:1B", "34:13:43", "78:6D:EB")
    for device in found:
        if device.address.upper().startswith(ouis):
            print(f"  target: {device.address} ({device.name})")
            return device.address
    sys.exit("no Cync-looking device is advertising right now")


async def one_attempt(
    mac: str, name: str, password: str, watch: float, index: int
) -> dict:
    """One connect/authenticate/subscribe cycle. Never raises - a failed
    attempt is a data point, not an error."""
    result = {
        "attempt": index,
        "connected": False,
        "authenticated": False,
        "subscribe_raised": None,
        "survived": 0.0,
        "notifications": 0,
        "held": False,
    }
    dropped = asyncio.Event()

    try:
        client = BleakClient(mac, disconnected_callback=lambda _: dropped.set())
        await asyncio.wait_for(client.connect(), timeout=25)
    except Exception as exc:
        result["subscribe_raised"] = f"connect failed: {type(exc).__name__}: {exc}"
        return result

    result["connected"] = True
    try:
        session = BleMeshSession(client, mac, name, password)
        try:
            result["authenticated"] = bool(await session.authenticate())
        except Exception as exc:
            result["subscribe_raised"] = f"auth failed: {exc}"
            return result
        if not result["authenticated"]:
            result["subscribe_raised"] = "mutual auth rejected"
            return result

        address = mac_to_address(mac)
        session_key = session._session_key

        def on_notify(_sender, data: bytearray) -> None:
            result["notifications"] += 1
            try:
                clear = decrypt_packet(session_key, address, bytearray(data))
                for st in parse_status(bytes(clear)):
                    result.setdefault("states", {})[st.device_id] = st.brightness
            except Exception as exc:
                result.setdefault("decode_errors", []).append(str(exc))

        started = time.monotonic()
        try:
            # Same order the integration uses: the vendor's own enable-write
            # first, then the standards-compliant subscribe that is under test.
            await client.write_gatt_char(NOTIFICATION_CHAR, bytes([0x01]), response=True)
            await client.start_notify(NOTIFICATION_CHAR, on_notify)
        except Exception as exc:
            result["survived"] = time.monotonic() - started
            result["subscribe_raised"] = f"{type(exc).__name__}: {exc}"
            return result

        # The load-bearing part: not whether the call returned, but whether
        # the link is still there afterwards.
        try:
            await asyncio.wait_for(dropped.wait(), timeout=watch)
            result["survived"] = time.monotonic() - started
        except TimeoutError:
            result["survived"] = time.monotonic() - started
            result["held"] = True
        return result
    finally:
        try:
            if client.is_connected:
                await client.disconnect()
        except Exception:
            pass


async def main() -> None:
    config, mesh_name, attempts = sys.argv[1], sys.argv[2], int(sys.argv[3])
    watch = float(sys.argv[4]) if len(sys.argv) > 4 else 20.0

    name, password = load_credentials(config, mesh_name)
    mac = sys.argv[5] if len(sys.argv) > 5 else await pick_target(mesh_name)
    print(f"mesh {name}, {attempts} attempts, watching {watch:.0f}s each\n")

    results = []
    for i in range(1, attempts + 1):
        result = await one_attempt(mac, name, password, watch, i)
        results.append(result)
        verdict = (
            "HELD"
            if result["held"]
            else ("dropped" if result["connected"] else "no connection")
        )
        print(
            f"  attempt {i:>2}: {verdict:>13}  "
            f"survived={result['survived']:5.1f}s  "
            f"notifications={result['notifications']:>3}  "
            f"{(result['subscribe_raised'] or '')[:40]}"
        )
        st = result.get("states") or {}
        if st:
            print(f"        decoded {len(st)} distinct device ids, sample: " + str(sorted(st.items())[:6]))
        await asyncio.sleep(2.0)

    print()
    connected = [r for r in results if r["connected"]]
    subscribed = [r for r in connected if r["subscribe_raised"] is None]
    held = [r for r in subscribed if r["held"]]
    print(f"attempts              : {len(results)}")
    print(f"connected+authed      : {len(connected)}")
    print(f"subscribe call OK     : {len(subscribed)}")
    print(f"link HELD full window : {len(held)}")
    if subscribed:
        times = [r["survived"] for r in subscribed]
        print(
            f"survival after subscribe: min={min(times):.1f}s "
            f"median={statistics.median(times):.1f}s max={max(times):.1f}s"
        )
        total_notifications = sum(r["notifications"] for r in subscribed)
        print(f"notifications received  : {total_notifications}")
    print()
    if held:
        print(
            "RESULT: at least one subscription survived the full window. Push is "
            "viable with retry, and cync_ble's give-up-for-30-minutes is wrong."
        )
    elif subscribed:
        print(
            "RESULT: every accepted subscription still lost the link. Retrying "
            "harder does not help on this firmware; the backoff is correct."
        )
    else:
        print("RESULT: no attempt got far enough to say anything. Inconclusive.")


asyncio.run(main())
