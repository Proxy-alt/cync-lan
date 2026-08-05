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
not an explicit `ATT_ERROR_RESPONSE`.

> **Correction.** This section originally called that a *different* failure
> mode from BlueZ's, on the basis that `StartNotify` "got an explicit ATT error
> `0x0E` ("Unlikely Error") back from the device". **It does not come from the
> device.** An on-air ATT capture (`probes/att_monitor.py`) taken while
> `start_notify` returned `UNLIKELY_ERROR` shows no `ERROR_RESPONSE` with
> `req=0x12` and no `error=0x0e` anywhere — BlueZ synthesises it locally when
> its own write goes unanswered. The two failure modes are therefore the *same*
> one seen through different stacks: silence on the wire.
>
> The same capture shows BlueZ never issuing a `FIND_INFORMATION` for `0x0013`
> at all, so the handle was never confirmed to exist by anything except BlueZ's
> own assumption that a `notify` characteristic must have a CCCD at
> value-handle-plus-one. See `ble_no_cccd_exists_at_all.md`.

The silence is itself the signal, since the ATT spec requires a Write Request
to always receive either a Write Response or an Error Response. In the same
capture, the pairing characteristic and the vendor enable write both *are*
acknowledged — so this is not a device that fails to answer writes generally.

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

## What this changes about the iOS hypothesis — UPDATE: resolved, and revised

The previous session's message speculated that CoreBluetooth's fully
spec-compliant `setNotifyValue` might succeed where BlueZ's non-identical
`StartNotify` sequence failed — i.e., that the wall was implementation-specific
to BlueZ. This test weakened that hypothesis by showing a hand-crafted,
sequence-independent CCCD write also fails, via a different failure mode
(silence, not an explicit reject).

At the time, this file went on to speculate that the real apps on **both**
platforms most likely avoid calling the platform's standard subscribe API
against this characteristic at all — confirmed for Android, guessed for iOS
by architectural analogy. **That guess for iOS was wrong.** Static analysis
of the real, decrypted iOS IPA (see `ble_ios_app_subscribe_confirmed.md`)
found the vendor's own `CbyGEKit.framework` *does* call
`setNotifyValue:forCharacteristic:` against this exact characteristic, using
the standard CoreBluetooth API, with no way to skip the CCCD write - exactly
as originally hypothesized before this file's own test result muddied it.

The better-supported reading now, combining both results: the CCCD
write/subscribe against this characteristic is genuinely unreliable for
**every** client that attempts it, including Apple's own first-party stack
driven by the vendor's real app. The vendor's own code contains dedicated
`subscribeRetryCounter` / `subscriptionRetryTimer` machinery and log strings
like `"Unable to subscribe to Telink Status characteristic on "` - i.e., they
built retry infrastructure because this exact call is known to fail in
production, not because it never gets attempted. This test's timeout (rather
than an explicit ATT error) is consistent with that: a genuine intermittent
firmware weakness under real conditions, not a deliberate BlueZ-specific or
Linux-specific rejection.

**Still not confirmed:** whether the failure mode is mesh-traffic contention
as hypothesized, a different firmware bug entirely, or something about this
specific node's role as an active relay - and whether the vendor's retry loop
typically succeeds on some attempt, or regularly exhausts its retries too.
Testing against a mesh node that is *not* mid-relay, or at a moment of lower
mesh chatter, would help separate these - not attempted here.
