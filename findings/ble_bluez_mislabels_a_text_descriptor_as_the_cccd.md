# BlueZ writes notification-enable bytes into a text label

**Status: settled on hardware, three ways.** The Telink notify characteristic
(`...1911`) declares the `notify` property and has **no CCCD**. The single
descriptor it does have, at handle `0x0013`, is a **`0x2901` Characteristic
User Description** whose value is the ASCII string `"Status"`.

BlueZ reports that same handle as a `0x2902` CCCD and writes `0100` into it.

```
BlueZ lists descriptor 00002902-0000-1000-8000-00805f9b34fb handle=0x0013
READ handle=0x0013 -> OK 537461747573        # "Status"
READ handle=0x0016 -> OK 436f6d6d616e64      # "Command"
```

On the air, that read is an ordinary successful ATT exchange:

```
TX->dev  READ_REQ 1300
RX<-dev  READ_RSP 537461747573
```

The attribute is real, readable, and correctly typed by the device. Two stacks
look at it and disagree about what it is:

| stack | what it calls handle 0x0013 | what it does |
| :--- | :--- | :--- |
| **CoreBluetooth** | `0x2901` Characteristic User Description | reports no CCCD, `setNotifyValue` fails cleanly, link survives |
| **BlueZ** | `0x2902` CCCD | writes `0100` into it, gets silence, synthesises `UNLIKELY_ERROR` |

CoreBluetooth is reading the UUID the device declared. BlueZ is overriding it
with an assumption: the characteristic says `notify`, therefore the handle
after its value handle must be a CCCD.

The capture shows BlueZ never checking. Its descriptor discovery issues
`FIND_INFORMATION` for `0x0004`, `0x0016`, `0x0019` and `0x001c` - the slot
after every *other* characteristic, each answered `0x2901` - and **skips
`0x0013` entirely**, the one slot it had already decided about. The
discriminator is exact: it queries the descriptor slot after every
characteristic *except* the one declaring `notify`.

## Not the cache: proven by deleting it

The obvious alternative was stale state — BlueZ caching a `2902` from some
earlier session and never re-checking. Tested directly, and it is not that.

**The exhibit, from BlueZ's own cache file** (`/var/lib/bluetooth/<adapter>/
cache/<device>`) for a Cync node:

```
0011=2803:0012:1a:00010203-0405-0607-0809-0a0b0c0d1911   ← notify (props 0x1a)
0013=00002902-0000-1000-8000-00805f9b34fb                ← BlueZ: CCCD
0014=2803:0015:0e:...1912
0016=00002901-0000-1000-8000-00805f9b34fb                ← User Description
0017=2803:0018:06:...1913
0019=00002901-0000-1000-8000-00805f9b34fb                ← User Description
001a=2803:001b:0a:...1914
001c=00002901-0000-1000-8000-00805f9b34fb                ← User Description
```

Every descriptor BlueZ **asked** about is recorded `2901`. The single one it
never asked about is recorded `2902` — and it is the slot following the only
characteristic whose properties include `notify`.

**Then `bluetoothctl remove` and rediscover from scratch.** Cache confirmed
gone (zero `2902` lines). On the fresh connection:

- descriptor discovery issued `FIND_INFORMATION` for `0x0004`, `0x0016`,
  `0x0019`, `0x001c` — **`0x0013` skipped again**;
- bleak still reported `descriptor 00002902 handle=0x0013`;
- reading `0x0013` still returned `537461747573`, `"Status"`;
- and BlueZ **wrote `0013=00002902-…` back into the newly created cache**,
  having never asked the device.

So the mislabel is not stale state to be cleared. It is regenerated on every
fresh discovery, and then persisted. A second node (`F4:BC:DA:33:52:66`) had no
cached GATT at all — only `Name=` — and behaved identically, which rules the
cache out from the other direction too.

## Why the handle exists but the descriptor does not

`ble_cccd_isolated_write_test.md` records a CCCD at `0x0013` "confirmed twice",
once from a `bleak --gatt` dump. That dump is **not independent of BlueZ** -
bleak on Linux reports the GATT table BlueZ hands it, mislabel included. The
handle is real; the *type* was never the device's claim.

That also explains the silence when it is written. `0x2901` is a read-only text
attribute, and `0100` is not a name. The device does not answer the write - it
should return `WRITE_NOT_PERMITTED` and instead returns nothing, which is its
own small non-compliance - but the write was never going to do anything even
if acknowledged.

### Settled by capture: 0x0E is BlueZ's, not the device's

An ATT capture off the HCI monitor socket (`probes/att_monitor.py`), taken
while `start_notify` ran and returned `UNLIKELY_ERROR`, closes this.

**Three things the capture shows.**

**1. BlueZ never asks whether 0x0013 exists.** Its descriptor discovery issues
`FIND_INFORMATION` for `0x0004`, `0x0016`, `0x0019` and `0x001c` — the slots
after every *other* characteristic — and each returns a `0x2901`. The slot
after the notify characteristic's value handle (`0x0012`) is `0x0013`, and it
is **never queried**, in either of the two connections captured. BlueZ assumes
a CCCD at value-handle-plus-one from the `notify` property and writes there
blind.

**2. The device never answers that write.**

```
TX->dev  WRITE_REQ handle=0x001b value=0c9c9a...   ← pairing
RX<-dev  WRITE_RSP                                  ← answered
TX->dev  WRITE_REQ handle=0x0012 value=01           ← vendor enable
RX<-dev  WRITE_RSP                                  ← answered
TX->dev  WRITE_REQ handle=0x0013 value=0100         ← the "CCCD"
         (nothing)
```

Both real attributes acknowledge their writes. `0x0013` gets silence. The ATT
spec requires a Write Request to be answered by a Write Response or an Error
Response; no attribute produces neither.

**3. No `0x0E` appears on the air at any point.** Every `ERROR_RESPONSE` in the
capture is `req=0x08`/`req=0x10` with `ATTRIBUTE_NOT_FOUND` — the ordinary
end-of-discovery markers. **None carries `req=0x12`, and none carries
`error=0x0e`.** So the `UNLIKELY_ERROR` bleak surfaces is **manufactured by
BlueZ** when its own write times out.

`ble_cccd_isolated_write_test.md` attributes that `0x0E` to the device. That
attribution is wrong and is corrected there.

### And the notifications come from the vendor write, not the CCCD

The two connections in the capture happen to form a controlled comparison:

| connection | enable write to `0x0012` | write to `0x0013` | notifications |
| :--- | :--- | :--- | ---: |
| first | yes, acknowledged | yes, unanswered | **20** |
| second | no | yes, unanswered | **0** |

The CCCD write alone produces nothing. The vendor value write is what starts
reporting — which is what this document already argued, now isolated rather
than inferred.

## Why this replaces the previous explanation

Everything written here previously said the device *refuses* the CCCD write —
"sometimes `WRITE_NOT_PERMITTED`, sometimes no ATT response at all". That was
the right observation and the wrong model.

There is nothing to refuse. BlueZ is writing to a descriptor that does not
exist, and the varying errors are what a stack produces when it probes an
absent handle. The three stacks then diverge entirely on policy:

| stack | what it does about the CCCD | outcome |
| :--- | :--- | :--- |
| **Android** | never writes one — `setCharacteristicNotification` is local-only | works |
| **CoreBluetooth** | reads the declared UUID, finds no `0x2902`, fails the call | **errors, keeps the link** |
| **BlueZ** | assumes one at value-handle-plus-one and writes into whatever is there | **synthesises an error, tears the link down** |

So the failure was never about this device being hostile to subscription. One
stack invented a descriptor that was never declared, wrote into an unrelated
attribute, and escalated the resulting silence into a destroyed connection.

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
bundle, in `scripts/corebluetooth/`, and the handle probe in `probes/`.

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

## Both OUI families, and the contrast test

`78:6D:EB` has the identical layout: `0x2902` claimed by BlueZ at `0x0013`,
`"Status"` returned by the device, and `0x2901` at `0x0016`/`0x0019`/`0x001c`
reading `"Command"`, `"OTA"`, `"Pair"`. So this is the Telink SDK's table, not
one product line.

**And the CCCD write is not what enables notifications.** Driven over
`HCI_CHANNEL_USER` with bumble as its own ATT client - BlueZ entirely out of the
path, `subscribe()` never called, subscriber registered locally:

```
notifications received : 21
distinct devices seen  : 42
CCCD writes performed  : 0
connection still up    : True
```

The capture confirms it independently: zero writes to `0x0013`, 21
notifications. The only writes are pairing (`0x001b`) and the vendor enable
(`0x0012`), both acknowledged.

## A caveat on the per-family split

`cync-ble` 0.2.0 recorded a clean split - `F4:BC:DA` hanging, `78:6D:EB`
returning `WRITE_NOT_PERMITTED` - across 13 nodes. A `78:6D:EB` node has since
returned `UNLIKELY_ERROR` (the timeout path) instead, so **the split is not
reliably by family**. Since both families have the same GATT table, a
structural explanation is ruled out; timing or firmware revision is more
likely. `cync-ble`'s code measures whether a node actually delivered a sweep
rather than inferring from its OUI, so the behaviour is unaffected - but the
stated rationale was over-confident.

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

> The device declares a `notify` characteristic with no CCCD - which is
> non-compliant. BlueZ responds by *assuming* a CCCD it never discovered,
> writing notification-enable bytes into the `0x2901` text descriptor that
> actually occupies that handle, and escalating the resulting silence into a
> destroyed connection. Android and CoreBluetooth both keep working.

That is a sharper claim than "BlueZ handles the refusal badly", and it is
falsifiable in one command: read handle `0x0013` and see a string.

`bleak-bumble` works for the same reason Android does: as its own ATT client it
never consults a descriptor it does not need.

## What this does not excuse

The device is still non-compliant - a characteristic declaring `notify` is
required to expose a CCCD, and this one does not. BlueZ's assumption is a
reasonable shortcut against compliant hardware. The bug is not the assumption;
it is that BlueZ never verifies it, writes into whatever attribute is at that
handle, and treats the outcome as fatal.
