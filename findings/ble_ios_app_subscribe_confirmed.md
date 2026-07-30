# The iOS app does call setNotifyValue — and expects it to fail sometimes

**Status: confirmed via static analysis of the real, decrypted iOS IPA
(`com.ge.cbyge1` v6.23.2, "Cync").** This settles the open question from
`ble_cccd_isolated_write_test.md` more precisely than hardware testing could:
the iOS app does not avoid CCCD subscription the way the Android app does.
It calls the standard, fully spec-compliant CoreBluetooth subscribe API
against the exact same characteristic this project has been probing — and
its own code shows the vendor built dedicated retry machinery because that
call is known to fail.

## What was inspected

The user provided a decrypted IPA (FairPlay already stripped, so the Mach-O
binaries are directly analyzable) and confirmed it runs under PlayCover on
their Mac, reaching as far as a home-selection screen before an unrelated
issue stops it going further. No runtime access was needed for this finding —
static inspection of the installed app bundle
(`~/Applications/PlayCover/Cync.app`) was sufficient.

The main `Cync` executable itself has no CoreBluetooth peripheral-level
selectors at all — only `CBCentralManagerDelegate` type encodings (scan/
connect level). The actual GATT work lives in an embedded framework:
`Frameworks/CbyGEKit.framework/CbyGEKit`, which:

- Links `CoreBluetooth.framework` directly
- Implements the full `CBPeripheralDelegate` protocol: `didDiscoverServices`,
  `didDiscoverCharacteristicsForService`, `didUpdateNotificationStateForCharacteristic`,
  `didUpdateValueForCharacteristic`, `didWriteValueForCharacteristic`, etc.
- Contains the literal selector `setNotifyValue:forCharacteristic:`
- Contains the exact same five Telink service/characteristic UUIDs this
  project has used throughout: `00010203-0405-0607-0809-0A0B0C0D1910`
  through `...1914`, byte-for-byte

This is the same GE/Savant Telink integration this project has been
reverse-engineering, confirmed from the vendor's own iOS binary rather than
inferred from the Android decompile or hardware behavior alone.

## The load-bearing strings

```
Subscribing to Telink Status characteristic on
Telink status notifications enabled on
Unable to subscribe to Telink Status characteristic on
Handle Telink Notification for: device
```

— a plain, expected subscribe-success/subscribe-failure pair around the
notify characteristic (`...1911`, the same one `ble_cccd_isolated_write_test.md`
tested by hand). But alongside it:

```
subscribeRetryCounter
subscriptionRetryTimer
subscriptionRetryTimers
Retrying subscription for device
```

Dedicated retry-counter and retry-timer infrastructure, specifically for
subscription. This is not defensive boilerplate for a call that "just
works" — retry timers with counters are what you build when a call fails
often enough in production to need automatic recovery.

(Separately, `SubscribeHubOperation` / `xlinkDeviceSubscriptionFailed` /
`onSubscription:withResult:withMessageID:` are a **different** subscribe
concept — cloud-side push subscription for WiFi/Xlink hub devices, unrelated
to BLE GATT. Kept distinct here because the naming overlaps enough to
conflate them by accident.)

## Why this matters

`ble_cccd_isolated_write_test.md` found that a bare, hand-crafted, spec-legal
CCCD write to this same characteristic **timed out** — no ATT response at
all — while the connection stayed alive and kept receiving unsolicited
notifications on a channel that had never been subscribed. That test's
closing section speculated the real apps on both platforms might avoid
calling the standard subscribe API entirely, the same way Android's
decompile proves it does.

**That reframing was right for Android and wrong for iOS.** The iOS app
does call the standard API, against the same characteristic, and the
vendor's own code treats subscribe timeout/failure as a known, handled
condition — not a hypothetical edge case. That is a strong independent
signal, from the vendor's own production client, that this characteristic's
subscribe path is **unreliable for everyone**, not specifically broken by
BlueZ's or bumble's sequencing. The mesh-relay-contention hypothesis in that
file (the device may be busy pushing unsolicited notifications when a
Write Request for the CCCD arrives, and drops the response rather than
serializing it) is now better supported: it would explain why even Apple's
fully spec-compliant, first-party stack needs a retry loop against this
exact operation.

## What this does not establish

- No visibility into the actual retry count/backoff constants, or whether
  giving up after max retries has a further fallback (e.g. falling back to
  plain polling reads) — `readValueForCharacteristic:` exists in the binary
  but generically, not tied specifically to a status-polling fallback path
  by any string found. Confirming this would need disassembly or a runtime
  trace, neither done here.
- Whether, once subscribed successfully, the same busy-mesh condition can
  cause a later silent drop (this project has not seen BlueZ's subscribe
  *succeed* at all, only timeout/reject, so there's no "subscribed then
  broke" case observed yet on any platform).
- The 30-second idle-disconnect timer constant (`time30Seconds`) also
  appears in this framework, matching Android's `TelinkDeviceBleManager`
  exactly — further confirms the connect-on-demand/disconnect-on-idle
  architecture is shared vendor design, not platform-specific, but this was
  a side observation, not the focus of this pass.

## What this changes for `cync_ble`

Nothing about the `local_polling` decision — if anything it's reinforced:
the vendor's own first-party app needs retry machinery around subscribe and
still may not have a working real-time push path in every session. A
polling-based tier-1 integration is not a workaround for a limitation
specific to Home Assistant's stack; it's the same trade-off the real app's
own engineers built retry logic to paper over.

The raw-HCI finding (`ble_raw_hci_push_confirmed.md`) is unaffected — it
remains true that bypassing the "subscribe via CCCD" requirement entirely
avoids this failure mode altogether, on both platforms' evidence now
(Android structurally, iOS by simply never needing to when unsubscribed
delivery already works). That option is still real for a future,
non-default tier, independent of this finding.

## How this was done

Static inspection only, on the user's own Mac, of an already-decrypted IPA
of an app the user has an active account with:

```
file Cync.app/Cync                                   # confirm arm64 Mach-O
otool -L Cync.app/Cync | grep -i bluetooth            # confirm CoreBluetooth link
strings -a Cync.app/Frameworks/CbyGEKit.framework/CbyGEKit \
  | grep -iE 'setNotifyValue|subscri|telink|0d191'
```

No network calls, no account credentials touched, no code execution of the
app was required for this specific finding.
