"""Do Cync devices put any state in their BLE advertisements?

Everything this project does today assumes state only exists inside a
connection: connect, authenticate, write the vendor enable byte, and catch
the notification sweep before the firmware kills the link. That works, but it
costs a connection slot on a shared radio and is the source of every
reliability problem seen so far.

If any state rides in the advertising data instead, none of that is needed -
no connection, no slots, no CCCD, no contention. It would work on any adapter
and through any ESPHome proxy, and it is the shape Home Assistant most
prefers (`local_push`, for free).

Method: capture every distinct advertisement payload per device over a
window, change one device's state over BLE, capture again, and diff. Three
phases so a change can be seen to track the state rather than merely differ
once.

Usage:
    adv_state_probe.py <config.yaml> <mesh_name> <relay_mac> <device_id> <device_mac>
"""

from __future__ import annotations

import asyncio
import sys

from bleak import BleakScanner

OUIS = ("F4:BC:DA", "30:C0:1B", "34:13:43", "78:6D:EB")
CAPTURE_SECONDS = 20.0


def _fingerprint(adv) -> tuple:
    """Everything in an advertisement that could plausibly carry state."""
    return (
        tuple(sorted((k, bytes(v).hex()) for k, v in adv.manufacturer_data.items())),
        tuple(sorted((str(k), bytes(v).hex()) for k, v in adv.service_data.items())),
        tuple(sorted(str(u) for u in adv.service_uuids)),
        adv.local_name or "",
    )


async def capture(label: str) -> dict[str, set[tuple]]:
    """Every distinct payload seen per Cync device during the window."""
    seen: dict[str, set[tuple]] = {}

    def on_adv(device, adv) -> None:
        if not device.address.upper().startswith(OUIS):
            return
        seen.setdefault(device.address.upper(), set()).add(_fingerprint(adv))

    scanner = BleakScanner(detection_callback=on_adv)
    await scanner.start()
    await asyncio.sleep(CAPTURE_SECONDS)
    await scanner.stop()
    print(
        f"  [{label}] {len(seen)} devices, "
        f"{sum(len(v) for v in seen.values())} distinct payloads"
    )
    return seen


def describe(seen: dict[str, set[tuple]]) -> None:
    """What is actually in these advertisements at all."""
    any_mfr = sum(1 for v in seen.values() for f in v if f[0])
    any_svc = sum(1 for v in seen.values() for f in v if f[1])
    print(f"  payloads carrying manufacturer data: {any_mfr}")
    print(f"  payloads carrying service data     : {any_svc}")
    for address, fingerprints in list(seen.items())[:3]:
        for fingerprint in list(fingerprints)[:1]:
            print(f"    {address}: mfr={fingerprint[0]} svc={fingerprint[1]}")
            print(f"      uuids={fingerprint[2]} name={fingerprint[3]!r}")


def diff(before: dict, after: dict, label: str) -> set[str]:
    """Devices whose advertisement content changed between captures."""
    changed = set()
    for address in before.keys() & after.keys():
        if before[address] != after[address]:
            changed.add(address)
    print(f"  [{label}] devices whose payloads changed: {len(changed)}")
    for address in sorted(changed)[:6]:
        gained = after[address] - before[address]
        print(f"    {address}: {len(gained)} new payload(s)")
        for fingerprint in list(gained)[:1]:
            print(f"      mfr={fingerprint[0]} svc={fingerprint[1]}")
    return changed


async def main() -> None:
    config, mesh, relay_mac, target, target_mac = (
        sys.argv[1],
        sys.argv[2],
        sys.argv[3],
        int(sys.argv[4]),
        sys.argv[5].upper(),
    )
    src = open("/home/hassio/state_decode_test.py").read().replace(
        "asyncio.run(main())", ""
    )
    namespace: dict = {}
    exec(src, namespace)  # noqa: S102 - reuse of the proven send()/credentials
    load_credentials, send = namespace["load_credentials"], namespace["send"]
    name, password = load_credentials(config, mesh)

    print("baseline capture...")
    baseline = await capture("baseline")
    describe(baseline)

    print(f"\nturning device {target} ON at full brightness...")
    await send(relay_mac, name, password, lambda s: s.set_power(target, True))
    await asyncio.sleep(2)
    await send(relay_mac, name, password, lambda s: s.set_brightness(target, 100))
    await asyncio.sleep(4)
    on_state = await capture("after ON")

    print(f"\nturning device {target} OFF...")
    await send(relay_mac, name, password, lambda s: s.set_power(target, False))
    await asyncio.sleep(4)
    off_state = await capture("after OFF")

    print("\n--- results ---")
    changed_on = diff(baseline, on_state, "baseline -> ON")
    changed_off = diff(on_state, off_state, "ON -> OFF")

    print()
    if target_mac in changed_on or target_mac in changed_off:
        print(
            f"PROMISING: {target_mac}'s own advertisement changed with its state. "
            "Passive state may be readable without connecting at all."
        )
    elif changed_on or changed_off:
        print(
            "Advertisements changed, but not on the device being driven - so this "
            "is probably ordinary churn (RSSI/counters), not state. Compare which "
            "devices changed across both phases before believing it."
        )
    else:
        print(
            "NEGATIVE: no advertisement content changed while a device was driven "
            "on and off. State does not ride in the advertising data; a connection "
            "remains the only way to read it."
        )


asyncio.run(main())
