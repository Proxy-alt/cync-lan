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

## Caveat worth keeping

Every decoded brightness in these runs was `0`. That is plausible — the house
may simply have been dark — but `parse_status`'s presence rule is the one
piece of this protocol marked "plausible, not confirmed", resting on a single
capture and contradicting acync. Before anything depends on harvested state,
it needs one capture taken with a known device deliberately **on**, to prove
the decode reports a non-zero brightness when it should. Until then, "34
device ids decoded" means the framing is right, not necessarily the values.

## How this was measured

`probes/subscribe_retry_probe.py`, against a wired switch on the mesh's own
`30:C0:1B:28:A2:64`, on the Raspberry Pi's built-in adapter under HAOS, with
Home Assistant running normally alongside. Target pinned explicitly after an
unpinned run picked a node that was advertising but not accepting connections
— worth noting, because "no connection" and "connected but refused" are
entirely different results and the scan order silently decides which you get.
