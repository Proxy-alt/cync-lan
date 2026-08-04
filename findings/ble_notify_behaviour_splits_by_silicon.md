# Two firmware behaviours to the same CCCD write, split by MAC family

**Status: settled on hardware, 13/13 clean split.** Cync nodes answer BlueZ's
Client Characteristic Configuration Descriptor write in two entirely different
ways depending on which OUI they carry, and only one of them leaves the harvest
able to collect anything.

| family | nodes that authenticated | `start_notify` | sweep |
| :--- | ---: | :--- | :--- |
| `F4:BC:DA` | 10 | **hangs** - no ATT response at all | 22 notifs, 38 of 46 devices, every time |
| `78:6D:EB` | 3 | **refused immediately**, `WRITE_NOT_PERMITTED` | 0, 0, and once 14 |

Ten out of ten, three out of three. No node behaved like the other family.

`30:C0:1B` matches `F4:BC:DA` (3/3 hung, 38 devices) from an earlier isolated
run; it went 0/3 connectable in the full sweep for reasons covered under
*Confounders* below.

## Why the difference decides everything

It is not cosmetic. bleak registers the notification callback and then writes
the descriptor:

- **Hang** - the write gets no answer, bleak sits waiting, and the callback
  stays registered. The vendor sweep flows into it for the whole window. The
  harvest's 4s timeout then walks away with a full picture of the house.
- **Immediate refusal** - bleak raises, the callback is discarded, and there is
  nothing to receive the sweep. One `78:6D:EB` node did catch 14 notifications
  before the raise landed, so it is a race rather than a hard zero - but it is a
  race that mostly loses.

So the harvest is not "unreliable on some nodes". It **cannot work** on
`78:6D:EB` nodes by construction, and works perfectly on `F4:BC:DA` ones.

## Not signal, and not device type

Signal was ruled out by spread. `F4:BC:DA` hung at -61, -62, -68, -68, -71,
-72, -76, -76, -82, -82 and -85 dBm alike. `78:6D:EB` refused at -63, -77 and
-87 - including the strongest node of the two families tested.

Device type tracks it less cleanly than the OUI does. `F4:BC:DA` spans types 48
and 53 and `30:C0:1B` is type 48, all hanging; `78:6D:EB` spans types 36, 55,
67 and 68, all refusing. Four different product types sharing one behaviour, on
one OUI, points at the silicon and its firmware rather than the product.

## The vendor enable-write is what makes the mesh report

Testing both subscribe methods on the same nodes separates the two effects that
were previously entangled:

| node | harvest (enable-write, then `start_notify`) | cccd (`start_notify` alone) |
| :--- | :--- | :--- |
| `F4:BC:DA:35:C9:CC` | HUNG, raw=22, 38 devices | HUNG, **raw=0** |
| `78:6D:EB:9E:06:40` | REFUSED, raw=0 | REFUSED, raw=0 |
| `78:6D:EB:9E:19:D8` | REFUSED, raw=14, 26 devices | REFUSED, raw=0 |

The `F4:BC:DA` row is the informative one. `start_notify` hung in both runs, so
the hang alone is not what produces data - with no enable-write in front of it,
**zero** notifications arrive. The vendor's `0x01` write to the notify
characteristic is what starts the reporting; the hang merely keeps the callback
alive long enough to catch it. Both are required, and this is the first test
that shows it rather than assuming it.

## What this means for cync_ble

`_known_good` records "completed a mesh handshake". Every one of these nodes
completes a handshake, including the ones that can never deliver a sweep, so
the flag does not mean what the candidate ordering needs it to mean.

The entry on the development box has it persisted as
`['786DEB9E0640', '786DEB9E14FD', 'F4BCDA385B6F']` - **two thirds from the
family that cannot harvest**. Proven nodes are tried first and
`MAX_CONNECT_ATTEMPTS` is 3, so a cycle can spend its whole budget on two nodes
that will refuse the descriptor, and report a failed harvest while 23 working
`F4:BC:DA` type-48 nodes sit untried.

The fix is to make the proven set mean "delivered a non-empty sweep", not
"authenticated".

This also retires a stale assumption written into `coordinator._candidate_macs`,
which says the `F4:BC:DA` family "consistently refuse connections" and that the
good nodes sit at the end of the cloud ordering. The opposite is true: on this
mesh `F4:BC:DA` is the family that works.

## Confounders, stated rather than explained away

**Connection failures in the full sweep are not a per-family measure.** 16 of 30
visible `F4:BC:DA` nodes failed to connect with `BleakError`, and the failures
cluster in the back half of the run - including `30:C0:1B` nodes that went 3/3
and `F4:BC:DA:38:5B:6F` which worked, both in earlier isolated runs minutes
before.

The cause is almost certainly scan staleness rather than node behaviour: the
sweep does one 25s scan up front and then reuses those `BLEDevice` objects for
the next several minutes, and BlueZ evicts device objects it has stopped seeing.
The error is the same `device 'dev_XX_..' not found` seen before. `cync_ble`
resolves through Home Assistant's Bluetooth manager, which keeps objects fresh,
so it is less exposed to this - but it is a real trap for any standalone script.

Notably this is *not* monotonic adapter exhaustion: three `78:6D:EB` nodes
connected fine at positions 34-36, in the middle of the failure zone.

**Each connection makes the next one harder** on the same node, which is why
`NODE_REST_SECONDS` exists. Pass 2 ran against nodes already disturbed by pass
1, so its two `BleakError` connect failures should not be read as a method
difference.

The `start_notify` split itself is unaffected by either confounder - it is
measured only on nodes that connected and authenticated, and it is unanimous
within each family.
