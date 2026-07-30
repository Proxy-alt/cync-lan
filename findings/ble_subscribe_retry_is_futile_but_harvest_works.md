# Retrying the subscribe never works — but you get a full mesh sweep anyway

**Status: confirmed on hardware, 14 attempts across two runs, zero variance.**

Two questions answered at once. The subscribe that `cync_ble` gives up on is
**never** going to succeed on this firmware, so imitating the iOS app's retry
loop is pointless. But the same failed attempt reliably delivers status for
**essentially the whole mesh** before it dies, which is a far better prize
than the subscription was.

## Why this was tried

`ble_ios_app_subscribe_confirmed.md` found the vendor's own iOS app ships
`subscribeRetryCounter` / `subscriptionRetryTimer` around this exact call.
Retry machinery is only worth building for something that *sometimes* works,
so the obvious question was whether persistence is the missing ingredient —
`cync_ble` had been giving up after a single attempt, on the strength of a
single observation.

One observation is not a rate. This measured it.

## The result

Each attempt: connect, authenticate, vendor enable-write (`0x01`), then
`start_notify`, then watch until the link dies or the window closes.

```
attempt  1:  dropped  survived=30.2s  notifications=19  UNLIKELY_ERROR
             decoded 38 distinct device ids
attempt  2:  dropped  survived=30.1s  notifications=17  UNLIKELY_ERROR
             decoded 34 distinct device ids
attempt  3:  dropped  survived=30.2s  notifications=18  UNLIKELY_ERROR
             decoded 34 distinct device ids
...
attempts              : 14   (across two runs)
subscribe call OK     : 0
link HELD full window : 0
```

**Every single subscribe was refused with GATT `UNLIKELY_ERROR` (0x0E), after
a consistent ~30 second delay.** No variance whatsoever — not one attempt
behaved differently. Retrying is not a strategy here; the answer is the same
every time.

So the iOS retry loop is not evidence that this call sometimes succeeds. More
likely it exists because the call sometimes *times out* rather than erroring,
and the vendor needed to recover from that — or because it works on hardware
revisions this mesh does not contain. Either way: **imitating it buys
nothing.**

## The part that matters more

Look at the middle column. **Every attempt delivered 17-19 notifications, and
those decoded to status for 34-38 distinct device ids** — near-complete
coverage of a 46-node mesh, in about 30 seconds, on every single try.

This is the mechanism already described in `ble_notifications_vs_sending.md`,
now measured: the vendor's `0x01` enable-write starts the device reporting,
BlueZ registers the callback locally the moment `start_notify` is called, and
notifications flow the entire time the CCCD write is pending. The rejection
lands ~30s later and takes the link with it — but by then the data has
already arrived.

The subscription failing is not the interesting event. **The connection is a
30-second window during which the whole mesh reports itself**, and it costs
one sacrificial link.

## What this means for `cync_ble`

It changes the integration's ceiling. The current design is honest but poor:
entities assume whatever they last commanded, so a physically-operated switch
is invisible forever. This gives a real alternative:

- a **command** connection that never subscribes and stays healthy
  indefinitely — what the integration already does, and what makes toggles
  work today;
- a periodic **harvest** connection that deliberately subscribes, accepts that
  it will be destroyed ~30s later, and collects a full mesh state sweep before
  it goes.

That is genuinely `local_polling` rather than `assumed_state`, and it is
close to what `cync2mqtt` does (see `cync2mqtt_status_model.md`: a periodic
sweep, availability derived from whether devices answer). **Not yet
implemented.**

## Caveat resolved — `parse_status` is correct

Every decoded brightness in the runs above was `0`, which was consistent with
both "the decode is right and the house was dark" and "the decode always says
zero". That has now been settled by driving a device to known levels over BLE
and harvesting after each change:

```
baseline                   device 16: 0     (38 ids decoded)
after ON + brightness 60   device 16: 60    (38 ids decoded)
after brightness 25        device 16: 25    (38 ids decoded)
after OFF (retried)        device 16: 0     (38 ids decoded)
```

**The decode reports exactly what was set, twice, at distinct values, and
returns to zero when the device is switched off.** Matching 60 and then 25 in
sequence is not something a broken decode does by accident.

So `parse_status`'s presence rule — a zero second byte marks the
*data-bearing* slot, inverting acync's rule — is **confirmed on hardware**,
not merely plausible. It has carried a "one capture, contradicts a
known-working implementation" warning since it was written; that warning can
come off. Two further consequences:

- `brightness > 0` is a sound on/off test, which is what `cync_ble`'s
  switch and light entities already assume.
- the harvested sweep is real, usable device state, not just correctly-framed
  noise.

One timing note, learned by getting it wrong: the harvest immediately after
the OFF command still reported `25`. A second attempt a few seconds later
reported `0`. State propagates through the mesh at its own pace, so a harvest
taken straight after a command can legitimately show the previous value - a
poller must not treat one stale reading as a failed command.

## How this was measured

`probes/subscribe_retry_probe.py`, against a wired switch on the mesh's own
`30:C0:1B:28:A2:64`, on the Raspberry Pi's built-in adapter under HAOS, with
Home Assistant running normally alongside. Target pinned explicitly after an
unpinned run picked a node that was advertising but not accepting connections
— worth noting, because "no connection" and "connected but refused" are
entirely different results and the scan order silently decides which you get.
