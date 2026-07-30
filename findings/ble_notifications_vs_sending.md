# On BlueZ you can send or receive, not both

**Status: settled for BlueZ + bleak, with the root cause now confirmed from the
real Android app's own decompiled source — and it points at a genuine, untried
fix, not just an explanation.**

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

## Confirmed from the decompile: the real app has exactly the same shape, and it's the root cause

`TelinkDeviceBleManager` (built on Nordic's `BleManager`) does the connection
setup for every characteristic in one pass. For the notify characteristic
(`...1911`), the entire notification setup is:

```java
gatt.setCharacteristicNotification(bluetoothGattCharacteristicM14996d, true);
this.f36846t = bluetoothGattCharacteristicM14996d;
```

**No CCCD write anywhere.** An exhaustive grep of the whole decompiled tree for
`writeDescriptor`, the CCCD UUID `2902`, or any `CLIENT_CHARACTERISTIC_CONFIG`
constant returns nothing. `setCharacteristicNotification()` is the load-bearing
fact here: on Android it is **purely local** — it tells the Android Bluetooth
stack "route incoming notification PDUs to this app" and performs *no GATT
operation over the air at all*. The characteristic value is never written with
`0x01` at bind time either; that write happens separately, on demand, as the
vendor's own "start reporting" command — the same one `python-dimond` sends and
this project's probe uses.

So the real app's notification setup and `python-dimond`'s converge on the same
underlying behaviour, for the same reason: **neither ever performs a wire-level
CCCD write.** Android's API doesn't require one to arm local delivery; bluepy's
low-level ATT socket doesn't impose one either. BlueZ is the odd one out —
`bleak.start_notify()` calls BlueZ's `StartNotify` D-Bus method, which *does*
write the CCCD as an integral part of subscribing, and this firmware answers
that specific wire operation with a rejection that drops the link.

**This reframes the finding.** It is not "this protocol cannot push state over
BLE." It is "BlueZ's subscribe primitive performs a GATT operation neither the
real client nor any known-working reimplementation ever performs, and this
firmware does not tolerate that operation." The device streams notifications
unconditionally once locally armed and poked — CCCD state does not appear to
gate it at all, on the evidence of every implementation that avoids writing one.

One more confirmation from the same source: disconnection is **designed-in**,
not a failure. `TelinkDeviceBleManager` runs a 30-second idle-auto-disconnect
timer and only reconnects if something still actively wants the connection
(`reconnectIfNecessaryDelayed`, gated on live connection locks). The real app's
own architecture is connect-on-demand, disconnect-on-idle — i.e., the
sacrificial-session / poll-then-reconnect model this project arrived at
independently is not a workaround for a limitation. It is what the vendor's own
client does.

## What this means for `cync_ble` — unchanged for now, but the ceiling just moved

`iot_class` stays `local_polling`, because nothing above has been *demonstrated*
to fix it — this is root cause plus a lead, not a working patch. But the lead is
concrete and worth stating precisely:

**If BlueZ's local notification registration could be triggered without its
`StartNotify` D-Bus call performing the CCCD write, this device would very
likely stream notifications exactly as it does for the real app.** That is not
possible through `bleak`, whose only subscribe path is `StartNotify`, and BlueZ's
D-Bus API has no lower-level "arm locally, skip the wire write" option — the
CCCD write is intrinsic to how `StartNotify` is implemented, not a flag on it.

The way around that is to stop asking BlueZ to be the GATT client at all — a
userspace ATT/L2CAP stack (in the same spirit as bluepy, but maintained and
usable from a modern async Python stack — `bumble` is the obvious current
candidate) could plausibly register for notifications and send the vendor
enable-write without ever issuing a CCCD write, the same way bluepy and Android
both already do. **This has not been tried.** It would be new tooling, not a
config change, and it is the most promising untried avenue for recovering
`local_push`.

**The ESPHome Bluetooth proxy path remains the other untried avenue**, and is
simpler to test if a proxy is available: a proxy implements its own GATT client
rather than delegating to BlueZ, so it may sidestep this entirely, independent of
whether a userspace-ATT approach is ever built.

## What was not tried, and why

- **bluepy directly.** It would very likely work now that the mechanism is
  understood, and it remains a dead end for the integration regardless: Home
  Assistant's Bluetooth stack is bleak-based, and an integration cannot bring its
  own ATT implementation just for this device family.
- **Raw D-Bus, bypassing bleak.** No help — BlueZ's `StartNotify` performing the
  CCCD write is intrinsic to BlueZ's own GATT server implementation, not
  something bleak adds on top. Going around bleak to talk to BlueZ directly hits
  the identical wall one layer down.
- **A userspace ATT stack (e.g. `bumble`).** Not yet tried. This is the
  actionable lead above, not a dead end — flagged here only to be explicit that
  no attempt has been made yet, so it is not mistaken for a tested negative.

## How this was tested

`probes/ble_control_probe.py --notify-mode {subscribe-first,enable-first,
enable-only,cccd-direct}`, each on a fresh connection, against a wired Cync
switch on a Raspberry Pi's built-in adapter (`hci0`) under HAOS.
