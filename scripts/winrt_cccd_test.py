"""Does a non-BlueZ stack survive this firmware's refused CCCD write?

Run this on Windows. It answers two questions that Linux cannot answer about
itself, and it needs no mesh credentials to do it - only the GATT layer is
involved, so nothing sensitive is required or touched.

  Q1  When the stack writes the Client Characteristic Configuration
      Descriptor and the firmware refuses it, does the LINK SURVIVE?

      On BlueZ it does not: BlueZ tears the connection down after its own
      ~30s timeout, reporting LOCAL_HOST_TERMINATED. The device never asked
      for that. If WinRT keeps the link open past 60s, the teardown is
      BlueZ policy rather than an unavoidable consequence of the refusal -
      which is the crux of the argument that Home Assistant's Bluetooth
      stack, not this hardware, is what makes these devices unusable.

  Q2  Can WinRT connect to the 786DEB-family nodes at all?

      BlueZ sees them at -70 dBm and still fails every connection with
      "device not found", 0 for 4. If Windows connects to the same nodes,
      that failure is BlueZ's, not the hardware's.

Requirements:  Python 3.9+,  pip install bleak,  Bluetooth on, in range.
Usage:         python winrt_cccd_test.py
Runtime:       about 4 minutes. Paste the whole output back.
"""

from __future__ import annotations

import asyncio
import platform
import sys
import time

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    sys.exit("bleak is not installed. Run:  pip install bleak")

# Telink vendor GATT characteristics used by every Cync mesh node.
NOTIFY_CHAR = "00010203-0405-0607-0809-0a0b0c0d1911"

# OUI prefixes seen on this mesh. Used instead of a device list so the script
# stays self-contained and carries no account data.
FAMILIES = ("F4:BC:DA", "78:6D:EB", "30:C0:1B", "34:13:43")

SCAN_SECONDS = 20.0
CONNECT_PER_FAMILY = 3
HOLD_SECONDS = 60.0


def family_of(address: str) -> str | None:
    upper = address.upper()
    for prefix in FAMILIES:
        if upper.startswith(prefix):
            return prefix
    return None


async def scan() -> dict[str, list[tuple[str, int]]]:
    """Cync nodes this adapter can see, grouped by family."""
    print(f"[1/3] Scanning {SCAN_SECONDS:.0f}s for Cync mesh nodes...")
    seen: dict[str, int] = {}

    def on_detect(device, adv) -> None:
        if family_of(device.address):
            seen[device.address] = adv.rssi

    scanner = BleakScanner(detection_callback=on_detect)
    await scanner.start()
    await asyncio.sleep(SCAN_SECONDS)
    await scanner.stop()

    grouped: dict[str, list[tuple[str, int]]] = {}
    for address, rssi in seen.items():
        grouped.setdefault(family_of(address), []).append((address, rssi))
    for nodes in grouped.values():
        nodes.sort(key=lambda item: item[1], reverse=True)

    print(f"      found {len(seen)} Cync node(s)")
    for prefix in FAMILIES:
        nodes = grouped.get(prefix, [])
        if nodes:
            span = f"{nodes[-1][1]}..{nodes[0][1]} dBm"
            print(f"      {prefix}  {len(nodes):>2} node(s)  {span}")
        else:
            print(f"      {prefix}   0 node(s)")
    return grouped


async def connectivity(grouped: dict[str, list[tuple[str, int]]]) -> list[str]:
    """Q2: which families will actually accept a connection here."""
    print(f"\n[2/3] Connection test, up to {CONNECT_PER_FAMILY} per family")
    print(f"      {'node':<20}{'rssi':<8}{'result'}")
    print("      " + "-" * 52)

    good: list[str] = []
    for prefix in FAMILIES:
        for address, rssi in grouped.get(prefix, [])[:CONNECT_PER_FAMILY]:
            started = time.monotonic()
            try:
                async with BleakClient(address, timeout=20.0) as client:
                    elapsed = time.monotonic() - started
                    result = f"connected in {elapsed:.1f}s"
                    good.append(address)
            except Exception as exc:
                result = f"FAILED {type(exc).__name__}: {exc}"
            print(f"      {address:<20}{rssi:<8}{result}")
    return good


async def cccd_test(address: str) -> None:
    """Q1: the refused descriptor write, and whether the link outlives it."""
    print(f"\n[3/3] CCCD test on {address}")

    disconnected_at: list[float] = []

    def on_disconnect(_client) -> None:
        disconnected_at.append(time.monotonic())

    client = BleakClient(
        address, timeout=20.0, disconnected_callback=on_disconnect
    )
    try:
        await client.connect()
    except Exception as exc:
        print(f"      connect failed: {type(exc).__name__}: {exc}")
        return
    print("      connected")

    notifications = 0

    def on_notify(_sender, data: bytearray) -> None:
        nonlocal notifications
        notifications += 1

    # The stack writes the CCCD here. On BlueZ this is refused with
    # WRITE_NOT_PERMITTED, or hangs and then takes the link down ~30s later.
    started = time.monotonic()
    outcome = "unknown"
    try:
        await asyncio.wait_for(
            client.start_notify(NOTIFY_CHAR, on_notify), timeout=20.0
        )
        outcome = f"ACCEPTED after {time.monotonic() - started:.2f}s"
    except asyncio.TimeoutError:
        outcome = "HUNG (no ATT response within 20s)"
    except Exception as exc:
        outcome = (f"REFUSED after {time.monotonic() - started:.2f}s - "
                   f"{type(exc).__name__}: {exc}")
    print(f"      start_notify: {outcome}")

    # The measurement that matters: does the connection outlive the refusal?
    print(f"      holding the link {HOLD_SECONDS:.0f}s to see if it survives...")
    hold_started = time.monotonic()
    while time.monotonic() - hold_started < HOLD_SECONDS:
        if disconnected_at:
            break
        await asyncio.sleep(1.0)

    if disconnected_at:
        died = disconnected_at[0] - hold_started
        print(f"      *** LINK DROPPED after {died:.1f}s - same as BlueZ")
    else:
        still = client.is_connected
        print(f"      *** LINK SURVIVED {HOLD_SECONDS:.0f}s "
              f"(is_connected={still}) - BlueZ does NOT")
    print(f"      notifications received meanwhile: {notifications}")

    try:
        await client.disconnect()
    except Exception:
        pass


async def main() -> None:
    print("=" * 62)
    print("Cync / Telink CCCD behaviour on a non-BlueZ stack")
    print(f"platform: {platform.platform()}")
    print(f"python:   {sys.version.split()[0]}")
    try:
        import importlib.metadata as md
        print(f"bleak:    {md.version('bleak')}")
    except Exception:
        pass
    print("=" * 62)

    grouped = await scan()
    if not grouped:
        print("\nNo Cync nodes visible. Move closer to a bulb or switch, "
              "confirm Bluetooth is on, and re-run.")
        return

    good = await connectivity(grouped)
    if not good:
        print("\nNothing accepted a connection, so the CCCD test cannot run.")
        return

    await cccd_test(good[0])

    print("\n" + "=" * 62)
    print("Done. Paste this entire output back.")
    print("=" * 62)


if __name__ == "__main__":
    asyncio.run(main())
