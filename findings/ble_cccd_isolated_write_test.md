# Isolating the CCCD write itself — it fails standalone too

**Status: confirmed on hardware.** A manually-sent, standard, spec-compliant
CCCD write — the exact value any compliant central (BlueZ, bumble's own
`subscribe()`, or iOS's `setNotifyValue`) would send, issued directly via
`write_value()` and never touching `subscribe()` at all — **also fails**, on a
fresh connection, isolated from any of BlueZ's or bumble's own subscribe
machinery.

## Why this test existed

Every avoidance path confirmed so far (Android, `bluepy`, `python-dimond`,
bumble-over-raw-HCI receiving with no CCCD write) shares one trait: none of
them ever perform a CCCD write. What had **not** been isolated was whether a
correctly-formed CCCD write, sent outside of `BleMeshSession`'s / bleak's /
bumble's own subscribe wrapper, succeeds or fails — i.e., whether the earlier
rejections were about **the CCCD write itself**, or specific to how BlueZ's
`StartNotify`/`AcquireNotify` or bumble's `Client.subscribe()` sequence it.

Motivated directly by a real question: CoreBluetooth on iOS is fully
spec-compliant and offers **no way to skip the CCCD write** — `setNotifyValue`
is the only door, and it always performs the write. Cync ships an iOS app. If
that app gets live device status, this firmware must accept a standards-
compliant subscription from *some* sequence — unless the iOS app also avoids
subscribing, the same way the decompiled Android app is confirmed to.

## What was done

Same connection setup as `ble_raw_hci_push_confirmed.md` (raw HCI via bumble,
pairing, mutual auth verified), then:

```python
await client.write_value(cccd_handle, struct.pack("<H", 1), with_response=True)
```

— the standard notify-enable bit (`1`), written directly to the CCCD handle
(`0x0013`, confirmed twice now: once from an independent `bleak --gatt` dump
earlier tonight, and again this run via the notify characteristic's own value
handle, `0x0012`, matching exactly). This bypasses `Client.subscribe()`
entirely — no higher-level convenience wrapper, just a bare `ATT_Write_Request`
to the exact right handle.

## Result

```
CCCD handle=0x0013 (from the earlier independent bleak dump, not re-discovered this run)
CCCD write: failed with TimeoutError: GATT timeout for ATT_WRITE_REQUEST
link alive after CCCD write attempt: True
registered local notification callback (CCCD write was rejected above)
enable-write sent; listening 10s...
  ... 23 notifications decrypted, spanning ~20 device ids ...
still connected: True
notifications received: 23

SUMMARY: manual CCCD write REJECTED, link survived the full window, 23 notifications decrypted
```

The write **timed out** — no response within bumble's `GATT_REQUEST_TIMEOUT`,
not an explicit `ATT_ERROR_RESPONSE`. This is a **different failure mode**
from before: BlueZ's `StartNotify` and bumble's `subscribe()` both got an
explicit ATT error `0x0E` ("Unlikely Error") back from the device. This bare
write got silence — which is itself notable, since the ATT spec requires a
Write Request to always receive either a Write Response or an Error Response;
genuine timeout-to-silence is non-compliant behavior on the device's part,
distinct from an active, spec-legal rejection.

**The connection survived regardless** — unlike every prior CCCD-write
attempt via BlueZ/bumble's own subscribe machinery, which took the link down
with them. And the no-CCCD registration path still worked perfectly
afterward: 23 more status packets, decrypting cleanly, same shape as every
other run tonight.

## A plausible mechanism, explicitly unconfirmed

`!!! received notification with no subscriber` fired **before** our own CCCD
write even completed — this mesh node (`F4:BC:DA:33:52:66`, mesh id 37) was
already streaming unsolicited status covering the whole mesh the instant the
connection opened, independent of anything this script did. One plausible
explanation for the write timing out rather than being answered: the device's
ATT implementation is busy pushing a stream of unsolicited notifications when
the Write Request arrives, and something in how it handles that contention
drops the response rather than serializing it correctly. This would be a
firmware timing/robustness issue specific to this characteristic under mesh
relay traffic, not a deliberate security decision — plausible, not confirmed;
no attempt was made to isolate "quiet mesh" vs "busy mesh" conditions.

## What this changes about the iOS hypothesis

The previous session's message speculated that CoreBluetooth's fully
spec-compliant `setNotifyValue` might succeed where BlueZ's non-identical
`StartNotify` sequence failed — i.e., that the wall was implementation-specific
to BlueZ. **This test weakens that hypothesis.** A hand-crafted, correctly-
formed, sequence-independent CCCD write — about as "generic compliant client"
as it gets — also failed, just via a different failure mode (silence, not an
explicit reject).

The better-supported reading now: the real apps on **both** platforms most
likely never call the platform's standard subscribe API against this
characteristic at all. Confirmed for Android from the decompile
(`setCharacteristicNotification` only, no CCCD write, anywhere in the app's
source). Not independently confirmed for iOS — no Apple hardware or iOS
decompile available — but CoreBluetooth's shared `didUpdateValueFor` delegate
(the same callback for both read completions and notifications) is at least
architecturally consistent with a stack that, like bumble's raw ATT dispatch,
does not gate delivery on prior subscription state. If the iOS app also relies
on unsolicited delivery without ever subscribing, that would explain live
status on iOS without requiring this firmware's CCCD path to actually work for
anyone.

**Still not confirmed:** whether this failure mode (timeout, not rejection) is
mesh-traffic contention as hypothesized, a different firmware bug entirely, or
something about this specific node's role as an active relay. Testing against
a mesh node that is *not* mid-relay, or at a moment of lower mesh chatter,
would help separate these - not attempted here.
