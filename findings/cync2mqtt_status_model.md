# How cync2mqtt keeps state, and why we cannot copy it

Read in full — `src/cync2mqtt` (469 lines), `src/acync/__init__.py`,
`src/acync/mesh.py` — to answer one question: given that our hardware forces
polling, what does a working implementation actually do?

## Their model

One connection, held open, with notifications subscribed once at connect time.
`status_worker` then runs forever:

```python
while True:
    for device in ...: device.online = False        # assume nothing is there
    for network in ...:
        while not await network.update_status():    # poke the mesh
            ... publish offline, retry after 120s, give up after 4 tries
    await asyncio.sleep(0.2 * len(devices))         # let the sweep come back
    ... publish availability from whoever reported
    await asyncio.sleep(300)                        # every five minutes
```

Three things worth taking from this:

1. **`update_status()` is a poke, not a read.** It writes `0x01` to the
   notification characteristic and then reads it back. We tested that read
   directly: it returns `01`, the enable byte, and nothing else. The state does
   not come from the read — it arrives asynchronously as notifications.
2. **Availability is derived, not reported.** Everything is marked offline, the
   mesh is poked, and whatever answers within the window is marked online. There
   is no "offline" message from a device.
3. **The window is sized by device count** — `0.2 × len(devices)`. For this
   mesh's 54 devices that is about 11 seconds. Their poll period is **300
   seconds**.

## Why it does not transfer

Their whole design rests on a subscription that stays up. Ours cannot: BlueZ's
`StartNotify` is refused by this firmware and the refusal drops the link — see
`ble_notifications_vs_sending.md` for the full tested matrix. acync shipping a
bluepy backend alongside bleak is the same problem showing through: bluepy never
writes a CCCD, so it never trips this.

## What transfers anyway

The important observation is that **the notification burst is a complete sweep,
and losing the link afterwards costs nothing**. In every run here, subscribing
produced 16–20 status packets covering many device ids within a couple of
seconds, and only then did the connection die.

So the harvest finishes before the link does. That makes a sacrificial-session
model viable:

```
every N minutes:
    connect -> handshake -> enable-write -> subscribe
    collect the burst                       (a few seconds)
    the link dies on its own                (expected, not an error)
commands:
    separate connection, never subscribes   (confirmed working)
```

Which is not far from cync2mqtt's shape. They poke every 300 s and wait
`0.2 × n` for the answer; we would connect every 300 s and read the answer until
the link drops. Same cadence, same derived availability, different plumbing.

## Numbers worth reusing

| | cync2mqtt | notes |
|---|---|---|
| poll period | 300 s | a sensible default for us too |
| sweep window | `0.2 × devices` | ≈ 11 s at 54 devices |
| retry wait after a failed sweep | 120 s | |
| give up after | 4 consecutive failures | they kill the process; we would degrade |

## Loose end

They keep `device.online` per sweep and publish availability from it. That is a
better availability signal than anything the TCP path has, and it is worth
copying whether or not the sacrificial-session model is adopted.
