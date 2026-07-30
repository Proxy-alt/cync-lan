# Send AND receive on one connection — confirmed, by going under BlueZ

**Status: confirmed on hardware, twice, with decrypted content verified.** This
resolves the open question at the end of `ble_notifications_vs_sending.md`: it
was never "BLE can't push state here" — it was "BlueZ's own subscribe
mechanisms can't." A raw HCI client, bypassing BlueZ's D-Bus GATT layer
entirely, receives notifications from this firmware with no CCCD write at all,
and the connection survives.

## What was built and run

`bumble` (Google's userspace Bluetooth host stack) driving `hci0` directly via
a raw `HCI_CHANNEL_USER` socket (`hci-socket:0` transport), rather than through
BlueZ. This required temporarily taking the adapter away from BlueZ for each
test — see "Operational notes" below for how that was done safely, since this
Pi's `hci0` is the adapter behind `yalexs_ble`, `ibeacon`, and HA's core
`bluetooth` integration.

The mechanism, read from `bumble`'s own source before writing anything against
hardware:

- `gatt_client.Client.subscribe()` **does** write the CCCD — confirmed by
  reading it — so it is never called.
- `gatt_client.Client.on_att_handle_value_notification()` fires for **every**
  raw ATT notification PDU that arrives on the connection, unconditionally. It
  only consults `notification_subscribers` afterward to decide who to call, and
  merely logs a warning if nobody is registered — it never conditions delivery
  on a prior subscribe.
- Populating `client.notification_subscribers[handle]` directly, without ever
  calling `subscribe()`, means notifications are delivered with **no CCCD write
  ever sent**.

This is different in kind from the earlier failed attempt to do the same thing
against `bleak` (`_backend._notification_callbacks`): there, BlueZ's daemon was
still the gatekeeper deciding whether to deliver anything, and the Python-side
dict was just bookkeeping paired to the D-Bus `StartNotify` call. Here, there is
no intermediary daemon — the process talking raw HCI **is** the ATT client, so
there is no separate policy layer to route around.

## The result

Pairing, mutual-auth verification, notification registration (no CCCD),
the vendor's `0x01` enable-write, then a 10-second listen — twice, cleanly:

```
mutual auth: VERIFIED
registered local notification callback directly - NO subscribe(), NO CCCD write
enable-write sent; listening 10s...
  notify [opcode=0xDC] decrypted=96ae9b00000756dc110223710000257300000000
      id=35 brightness=0 temp=0
      id=37 brightness=0 temp=0
  ... (21 packets total, one run)
still connected: True
notifications received: 21
```

All 21 decrypt cleanly through `cync_lan.ble_mesh.decrypt_packet` +
`parse_status` — vendor `0x11 0x02` at bytes 8:9, opcode `0xDC`, spanning ~20
real mesh device ids. Not noise: the same shape confirmed correct every other
time this session, now arriving over a connection that was never subscribed.

**The connection was alive the entire 10-second window** — `still connected:
True` printed after the loop completed, before the script closed it itself.
This is the first time on this transport that receiving has not cost the link.

Confirmed twice, on separate power-cycles, with consistent results both times.

## Why python-dimond/Android never had this problem, restated precisely

Both avoid a wire-level CCCD write for the same underlying reason bumble-over-
raw-HCI does: none of them go through an intermediary daemon that enforces
"subscribe via CCCD before I deliver anything." Android's
`setCharacteristicNotification()` is local-only; bluepy speaks ATT over its own
socket; bumble-over-raw-HCI is the ATT client itself. BlueZ is the odd one out
precisely because it *is* such a daemon, and its two subscribe primitives both
insist on the wire operation this firmware refuses.

## What this does NOT yet establish

- **Send-while-subscribed, in the same session, has not been tested.** This run
  proved receive-while-connected; the next step is sending a real command (e.g.
  `set_power`) over `client.write_value` to the control characteristic
  *(`...1912`, `handle=0x0012` per this device's discovery — not yet resolved in
  this run, only the pairing and notify handles were)* while notifications are
  live, to prove the actual send-and-receive-together case end to end.
- **This is a hand-rolled prototype**, not packaged. Turning it into something
  `cync_ble` can use means either vendoring a minimal raw-HCI GATT client, or
  depending on `bumble` directly as an optional local-adapter backend behind the
  same `GattClient` protocol `cync_lan.ble_mesh` already defines - the protocol
  design already accommodates this without changes, confirmed by importing
  `cync_lan.ble_mesh`'s crypto with zero `bleak` import during this work.
- **Whatever BlueZ's normal GATT client role was doing for other integrations
  during the outage window is a separate question** - device connections held
  by `yalexs_ble`/`ibeacon`/core `bluetooth` were dropped for the duration and
  expected to recover on their own once the adapter was handed back; this was
  not independently verified beyond confirming `bluetoothctl show` reports the
  controller fully normal afterward.
- **ESPHome proxy compatibility remains untested and is now a secondary
  question** rather than the only lever - a raw-HCI approach works without
  needing a proxy at all, on any adapter BlueZ would otherwise manage.

## Operational notes, for repeating this safely

`hci-socket:0` binds `HCI_CHANNEL_USER`, which requires the adapter to be
powered off first (`bluetoothctl power off`) — attempting to bind while BlueZ
has it up fails cleanly with `EBUSY` and touches nothing (confirmed twice; a
failed bind is a safe no-op). After `power off`, confirm `Powered: no` before
proceeding — `bluetoothctl power off` can return before the kernel state
actually settles, and racing this produces the same harmless `EBUSY` rather
than corruption.

Release is automatic and standard Linux kernel behavior, not specific to this
OS: closing the `HCI_CHANNEL_USER` socket - including via process crash or
`SIGKILL` - causes the kernel to hand the adapter back, and BlueZ picks it up
again. Confirmed clean recovery across every cycle in this session, verified
each time via `bluetoothctl show` before declaring done. `bluetoothctl power
on` afterward is not required for recovery but was done anyway as an explicit,
verifiable restoration step rather than relying on implicit recovery alone.

The docker container needs `--net=host --cap-add=NET_ADMIN --cap-add=NET_RAW`
for the raw `AF_BLUETOOTH` socket; plain `--net=host` is not sufficient.

When running a multi-step remote sequence over SSH (power off, run container,
power on), **give each step its own SSH invocation with its own stdin** rather
than bundling them in one remote script sharing one piped-in file. Bundling
them caused the container to receive stale/partial script content in this
session - some earlier command in the sequence appears to consume from the
shared stdin stream first, even though it does not visibly need input.
