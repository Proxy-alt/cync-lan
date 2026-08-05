# The characteristic declares Notify and has no CCCD

**Status: confirmed on hardware via CoreBluetooth.** The Telink notify
characteristic (`...1911`) advertises the `notify` property and ships **no
Client Characteristic Configuration Descriptor**. Its only descriptor is
`0x2901`, Characteristic User Description.

Per the Bluetooth Core specification, a characteristic with the Notify property
**must** expose a CCCD (`0x2902`) — that descriptor is how a client subscribes.
This firmware declares the capability and omits the mechanism.

```
[*] Properties: read,write,writeNoResp,notify
[*] descriptor present: Characteristic User Description
[!] setNotifyValue REFUSED after 0.87s: The attribute could not be found.

*** LINK SURVIVED 60s after the CCCD write.
```

## Why this replaces the previous explanation

Everything written here previously said the device *refuses* the CCCD write —
"sometimes `WRITE_NOT_PERMITTED`, sometimes no ATT response at all". That was
the right observation and the wrong model.

There is nothing to refuse. BlueZ is writing to a descriptor that does not
exist, and the varying errors are what a stack produces when it probes an
absent handle. The three stacks then diverge entirely on policy:

| stack | what it does about the CCCD | outcome |
| :--- | :--- | :--- |
| **Android** | never writes it — `setCharacteristicNotification` is local-only | works |
| **CoreBluetooth** | looks for it, does not find it, returns "the attribute could not be found" | **errors, keeps the link** |
| **BlueZ** | mandates the write in `StartNotify`/`AcquireNotify`, has no way to skip it | **destroys the connection** |

So the failure was never about this device being hostile to subscription. It is
about one stack treating a missing optional-in-practice descriptor as fatal to
the connection, where the other two treat it as a failed operation.

## How notifications work at all, then

Through a **vendor value write**, not through subscription. Writing `0x01` to
the notify characteristic is what makes the mesh start reporting — that write
is accepted here (`vendor enable-write accepted`) and is an ordinary
characteristic write with nothing to do with the CCCD.

That resolves what used to look like a contradiction: on Linux, 22 notifications
covering 38 devices arrive *while* the subscribe attempt is hanging. They are
not arriving because of the subscription. They arrive because of the enable
write, and the hang merely leaves the callback registered long enough to catch
them. The harvest was exploiting the right mechanism for the wrong stated
reason.

## Method

Native CoreBluetooth, not bleak — the same API the vendor's own iOS app uses,
so no translation layer can be blamed for the result. Swift, built as an app
bundle, in `scripts/corebluetooth/`.

Two hurdles worth recording, because both cost time:

- **CoreBluetooth aborts any process whose bundle lacks
  `NSBluetoothAlwaysUsageDescription`.** `SIGABRT`, no traceback, no stdout.
  Both the python.org framework build and Homebrew Python die this way, which
  is why this is a Swift app and not a bleak script.
- **The bundle must be launched through LaunchServices.** Running
  `CyncCCCDTest.app/Contents/MacOS/CyncCCCDTest` directly still aborts;
  `open -W --stdout … CyncCCCDTest.app` works. TCC attributes the bundle only
  on the LaunchServices path.

Needs no mesh credentials — only the GATT layer is involved.

## Scope

Two peripherals, two runs, at -87 and -64 dBm, identical results. macOS hides
BD_ADDRs behind opaque per-host UUIDs, so **which OUI family these belonged to
is unknown** — the `F4:BC:DA` / `78:6D:EB` split measured on Linux cannot be
reproduced here. Whether the missing CCCD is uniform across families is
therefore not established, only that it holds for the two nodes reachable from
this host.

Zero notifications arrived, which is expected and not a finding: no mesh
authentication was performed, and an unauthenticated peer gets nothing.

## Consequence

This is a considerably stronger argument than the one in the existing gist. The
claim is no longer "BlueZ handles a refusal worse than other stacks" — it is:

> The device is non-compliant in a specific, nameable way. Two of three major
> BLE stacks tolerate it and keep working. BlueZ alone escalates a missing
> descriptor into a destroyed connection, and offers no API to opt out.

`bleak-bumble` works for exactly this reason: as its own ATT client it never
consults a CCCD it does not need, which is the same position Android is in.
