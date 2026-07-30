# On BlueZ you can send or receive, not both

**Status: settled for BlueZ + bleak. All four approaches tested on hardware.**

This firmware rejects the CCCD write that BlueZ performs when subscribing, and
the rejection takes the connection down. Every way around it available through
bleak has now been tried.

## The matrix

| approach | outcome |
|---|---|
| `StartNotify`, then the enable-write | subscribe refused (`Unlikely Error`), **link drops** |
| enable-write, then `StartNotify` | enable accepted, subscribe refused, **link drops** |
| enable-write only, never subscribe | **no notifications at all** |
| write the CCCD by hand | **bleak refuses**: *"Cannot write to CCCD (0x2902) directly. Use start_notify() or stop_notify() instead."* |
| never touch notifications | **sending works** — confirmed for power and brightness |

The device is not the obstacle in the last two rows. Row 3 is BlueZ: it will not
route notifications to a client that has not subscribed. Row 4 is bleak, which
deliberately blocks direct CCCD access because BlueZ owns that descriptor.

So on a local adapter the choice is binary. Send reliably and get no inbound
state, or subscribe once and lose the session.

## Why python-dimond does not have this problem

It uses **bluepy**, which does not go through BlueZ's D-Bus GATT API at all — it
speaks ATT over its own socket. Nothing forces a CCCD write, so writing `0x01` to
the notification characteristic's value is enough and notifications simply
arrive. That is why dimond's approach works and cannot be copied here.

`acync` shipping both a bleak and a bluepy backend now reads as a symptom of
exactly this, rather than a convenience.

## What it means for `cync_ble`

`iot_class` is `local_polling`, and this is why. A Home Assistant integration has
to both command devices and report their state; on BlueZ it cannot do both on one
session.

**The ESPHome Bluetooth proxy path is now the only remaining hope for pushed
state, and it is untested.** A proxy implements its own GATT client rather than
delegating to BlueZ, so it may not have this constraint at all — in which case
`local_push` becomes correct for proxy users while local-adapter users stay on
polling. That is the highest-value unknown left in the BLE work.

## What was not tried, and why

- **bluepy directly.** It would likely work, and it is a dead end regardless:
  Home Assistant's Bluetooth stack is bleak-based, and an integration cannot
  bring its own ATT implementation.
- **Raw D-Bus, bypassing bleak.** BlueZ's GATT D-Bus API has no CCCD write; it
  manages the descriptor itself through `StartNotify`. Same wall, one layer down.

## How this was tested

`probes/ble_control_probe.py --notify-mode {subscribe-first,enable-first,
enable-only,cccd-direct}`, each on a fresh connection, against a wired Cync
switch on a Raspberry Pi's built-in adapter (`hci0`) under HAOS.
