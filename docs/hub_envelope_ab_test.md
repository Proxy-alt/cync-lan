# Hub envelope A/B test, and two open items from the 2026-07-25 decompile pass

> **Mirroring**: `docs/` is mirrored across the three artifact repositories,
> and CI's `docs-in-sync` job compares the whole directory against
> [`cync-lan-lib`](https://github.com/Proxy-alt/cync-lan-lib), which is
> canonical. **Put any change there first, then copy**, or CI will fail in
> every repository that has the file.

Three items, in priority order. Item 1 concerns code that is already shipping;
items 2 and 3 are research state being recorded so it is not lost.

All decompile citations are against **v2** (`cync_decompiled_v2`), relative to
its `sources/` root. Method and field names there are R8-renamed and renumber
on every re-decompile — cite class paths, never member names. Fuller write-ups
live in that tree under `tools/findings/`, but everything load-bearing is
restated here, because a claim that cannot be re-derived from a class path is a
claim that will not survive losing the folder.

Evidence tiers are this repo's usual ones: **confirmed** (cited to an exact
class, a capture, or shipping behaviour), **plausible** (reasonable inference,
not proven), **not found** (looked for, not established).

---

## 1. The hub envelope may carry 7 bytes of routing the device does not expect

### What is shipped

Every hub-family command in `devices.py` — including the six wired in 0.3.0 —
builds its length field as:

```python
cmd_ = 8 + len(payload)   # routing(7) + op_prefix(1) + payload
```

and calls `broadcast_control_command(...)`, which reaches
`PacketBuilder.build_control_packet()` with `repeat_op_code` left at its
default `True`. That produces:

```
header(8) + routing(7) + op_prefix(1) + payload
```

### What the decompile says

**Confirmed.** In the phone app, the 7-byte routing prefix is prepended by one
specific method — the one this repo's `mesh_opcodes.md` already identifies as
`CommandDelegate.f()` (`sources/com/gelighting/cbygekit/services/devices/xlink/XlinkDeviceManager.java`,
around lines 984–1000). A sweep of all 15 hub-family command classes in
`sources/com/gelighting/cbygekit/services/devices/command/` found that **none
of them reaches that method.** They dispatch instead through
`XlinkTranslatorKt`'s frame builder or a `Frame` constructor, both of which
emit `[msgId][flag][op][len][payload][cksum]` with the payload starting
immediately after the length field.

This is structurally unsurprising: a hub command is not addressed to a mesh
device, so there is no mesh address to route to. It is also consistent with
this repo's existing observation that on the hub/xlink path the dispatch byte
*is* the outer op_code, unlike the `0x8E` mesh-relay family.

**Plausible, not confirmed:** that this transfers to cync-lan's wire. The app's
path is phone→device/cloud; cync-lan intercepts device→cloud. Different
direction, different socket. The app evidence establishes the *shape* of a hub
command, not that the device-facing relay uses the same shape.

**Not found:** any capture of a hub command on cync-lan's own wire.

### The two candidates

**A — shipped.** `cmd_ = 8 + len(payload)`, routing prefix present.
**B — no routing.** `cmd_ = 1 + len(payload)`, routing prefix omitted, op_prefix kept.

| Command | op | payload | A `cmd_` | A total | B `cmd_` | B total |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `delete_group` | `0x32` | 2 | 10 | 21 | 3 | 14 |
| `query_device_time` | `0x46` | 6 | 14 | 25 | 7 | 18 |
| `query_hub_info` | `0x4B` | 64 | 72 | 83 | 65 | 76 |
| `query_hub_mesh_credentials` | `0x8A` | 64 | 72 | 83 | 65 | 76 |
| `delete_automation` | `0x97` | 6 | 14 | 25 | 7 | 18 |
| `query_sol_config` | `0xAD` | 6 | 14 | 25 | 7 | 18 |

Exact bytes, `msg_id=0x00`, `target_id=0x00`, `sub_id=0x00`:

```
delete_group (group_address=0x8001)
  A: 7E 00 00 00 00 F8 32 0A 00 00 00 00 00 00 00 00 32 01 80 EF 7E
  B: 7E 00 00 00 00 F8 32 03 00 32 01 80 E8 7E

query_device_time
  A: 7E 00 00 00 00 F8 46 0E 00 00 00 00 00 00 00 00 46 00 00 00 00 00 00 9A 7E
  B: 7E 00 00 00 00 F8 46 07 00 46 00 00 00 00 00 00 93 7E

delete_automation (schedule_id=5)
  A: 7E 00 00 00 00 F8 97 0E 00 00 00 00 00 00 00 00 97 05 00 00 00 00 00 41 7E
  B: 7E 00 00 00 00 F8 97 07 00 97 05 00 00 00 00 00 3A 7E

query_sol_config
  A: 7E 00 00 00 00 F8 AD 0E 00 00 00 00 00 00 00 00 AD 00 00 00 00 00 00 68 7E
  B: 7E 00 00 00 00 F8 AD 07 00 AD 00 00 00 00 00 00 61 7E

query_hub_info                      (64-byte zero payload, elided)
  A: 7E 00 00 00 00 F8 4B 48 00 00 00 00 00 00 00 00 ... DE 7E   (83 bytes)
  B: 7E 00 00 00 00 F8 4B 41 00 4B 00 00 00 00 00 00 ... D7 7E   (76 bytes)

query_hub_mesh_credentials          (64-byte zero payload, elided)
  A: 7E 00 00 00 00 F8 8A 48 00 00 00 00 00 00 00 00 ... 5C 7E   (83 bytes)
  B: 7E 00 00 00 00 F8 8A 41 00 8A 00 00 00 00 00 00 ... 55 7E   (76 bytes)
```

### Why this difference is unusually clean

Hub commands are broadcast with `target_id = 0x00` and `sub_id = 0x00`, and
`msg_id` is `0x00` here, so **the entire 7-byte routing prefix is zeros**. The
two candidates therefore differ in exactly two ways:

1. the declared length (`cmd_`), by 7; and
2. the presence of 7 zero bytes between the header and the op_prefix.

Nothing semantic differs. That has a useful consequence: a device that reads
`len` and consumes that many bytes will behave differently under A and B, but a
device that ignores trailing bytes might accept both. Which means **a positive
result under A is decisive, while a negative result under A is not.**

### How to settle it

This plugs directly into `hardware_verification.md`'s **Step 2 (Hub clock,
read only)**, whose stated weakness is that failure is ambiguous. Candidate B
removes that ambiguity by supplying a concrete alternative to try.

1. **Run Step 2 as written.** If the Hub clock sensor reports a plausible
   time, **candidate A is correct** — a query that gets a well-formed reply
   proves the request was accepted as sent. Stop; record it; this item closes.
2. **If Step 2 fails**, re-send the same command as candidate B and watch for a
   reply. The cheapest way to do that without touching shipped behaviour is a
   one-off script that builds the B bytes above and writes them to the device
   socket; do not flip the constant in `devices.py` to test it.
3. **A reply under B and not under A** settles it the other way, and the fix is
   a hub-family-specific envelope, not a changed constant — see below.
4. **Neither** → the problem is not the routing prefix, and `hardware_verification.md`'s
   Step 3 (a hub-family *write* with a visible outcome) is the next
   discriminator, since it separates "request rejected" from "reply never
   arrives".

Debug logging already prints outgoing packet hex
(`custom_components.cync_lan` and `cync_lan` at debug), so the bytes above can
be compared against what actually goes out before anything is concluded.

### Result: neither envelope produces a reply — tested 2026-07-27

Run against real hardware. The **Hub clock sensor reports `unknown` under both
candidates**, which is the "neither" branch above: the request envelope is not
the discriminator, or at least not the only thing wrong.

`unknown` is the specific signal here, not `unavailable`. The sensor polls,
builds the command, sends it, and `query_device_time()` returns `None` when its
10-second wait for a notification expires — so the code path ran and nothing
came back. The distinction matters: `unavailable` would mean the entity was
never reached.

**What this rules out.** The 7-byte routing prefix is not the sole cause. If it
had been, exactly one of the two shapes should have produced a reply.

**What it does not rule out**, in rough order of likelihood:

- The reply arrives but is not recognised — parsed under the wrong notification
  type, or not correlated back to the request. Nothing in this repository has
  ever observed a hub-query *response* on real hardware, so the receive side is
  as unproven as the send side.
- The op_code or payload is wrong in some way the envelope work would not have
  caught. Both candidates were built from the same decompiled op table.
- The bridge in this particular installation does not implement hub queries at
  all. The op codes come from the phone app talking to *a* hub; nothing
  establishes that every WiFi device answers them.

**Next discriminator** is `hardware_verification.md`'s **Step 3** — a hub-family
*write* with a visible physical outcome. A write that visibly works proves the
request side is fine and moves all suspicion to the reply channel. A write that
does nothing keeps both halves in play, and at that point a packet capture stops
being optional.

Until then the six shipped hub commands stay experimental, and
`_warn_experimental_cmd_code` stays accurate for them.

### If B turns out to be right

The change is not just the `8 +` constant. `build_control_packet()` always
emits the routing block, so a correct fix needs a way to omit it for this
family — parallel to the existing `repeat_op_code` flag, which exists for
exactly this kind of family-specific structural difference. Six shipped
commands and the `_query_hub` helper would move onto it.

Until it is settled, the six shipped commands stay experimental and
`_warn_experimental_cmd_code` remains accurate — the predicted length may be
wrong by 7 for this whole family, not merely unconfirmed.

---

## 2. Multipart query responses — substantially resolved, and it was nearly missed

Three queries return chunked replies: `QueryLightShowSettingsCommand`,
`QueryMusicShowSettingsCommand`, `QueryMotionSensorScheduleCommand`. Without
the chunk header these can be sent but not decoded.

Two separate research passes reached opposite conclusions about this because
neither saw the other's output: the query pass recorded the reassembly as
untraced, while the multipart pass had **already documented the receive-side
layout**. The layout, restated here so it survives:

**Confirmed** —
`sources/com/gelighting/cbygekit/services/devices/telink/TelinkLightShowSettingsNotificationParser.java`:

```
sequence = dataBytes[12]
if sequence != 1:  part    = (sequence, dataBytes[13..])
else:              partCount = dataBytes[13]
                   startPart = (partCount, dataBytes[14..])
```

A 1-based sequence byte, with the **first** packet carrying an extra total-count
byte immediately after it. `AbstractMultipartNotificationJoiner`
(`sources/com/gelighting/cbygekit/services/devices/model/notification/`) sorts
the parts, asserts `part.sequence == index + 1`, concatenates, and hands the
joined buffer on. Completion is `parts.size() == partCount`.

This mirrors the send-side chunker exactly, and was derived independently of
it — which is decent mutual corroboration.

**Caveat, plausible not confirmed:** the *joined* light-show buffer is a
**report** layout, not the `SetLightShow*` request layout. It appears to merge
what the base and extended set commands send separately, with a 2-byte-LE ×100
speed encoding and an effect byte at `[1]`. Do not decode a reply with the
request layout.

**Still open:** whether the motion-sensor-schedule and music-show joiners use
the same `[12]`/`[13]` offsets, or only the light-show parser was read. That is
a small, well-defined read against the sibling `*MultipartNotificationJoiner`
classes in the same package.

---

## 3. The 3-byte header field carries the block index, not the msgId, on chunked sends

**Confirmed, and it contradicts a note marked confirmed-against-capture.**

`XlinkCommandDelegate`'s frame helper decides what goes in that field from a
bitmask supplied by the caller:

```java
mo14056h(bArr, meshAddress, getF37789c(), (i2 & 8) != 0 ? getF37789c() : i, cont)
```

(`sources/com/gelighting/cbygekit/services/devices/xlink/XlinkCommandDelegate.java`,
`DefaultImpls`.) With mask `12` the field gets the msgId; with mask `4` it gets
the caller's `i`. Every chunked command passes mask `4` with the **block
index** as `i`, so for those nine commands the field is a block index.

An inline note in
`sources/com/gelighting/cbygekit/services/devices/xlink/XlinkDeviceManager.java`
describes that field generally as "msgId (3B LE)". That is right for the
ordinary single-shot path and wrong for every chunked send. The note carries a
"confirmed against real hardware (packet capture)" tag, which is presumably
true of the case that was captured — single-shot — and was then over-generalised.

**Why it matters:** response correlation. If replies are matched by echoed
msgId, that assumption does not hold for chunked commands, because the field
does not contain a msgId.

**Not found:** any capture exercising a chunked command over the Xlink path.
Whether multi-block reassembly survives the `0x8E` relay at all is untested.

**Relevance to cync-lan today:** low and latent. No chunked command is wired
in, so nothing is broken now. It becomes load-bearing the moment light shows,
tile layouts, or bitmaps are implemented — which is why it is recorded rather
than left in a findings file.

---

## Provenance

Items above come from three targeted passes over
`cync_decompiled_v2` on 2026-07-25, retained in that tree at
`tools/findings/{multipart,query,hub}_commands.md`. The same passes found six
distinct bugs in that tree's extraction tooling; fixing them took mechanical
opcode coverage from 41 to 105 of 105 command classes, and the tool's derived
values then matched the manual reads independently (13/13 mesh opcodes, 17/17
hub ops). Regenerate the catalogue with `cyncdec opcodes`.

One correction to record: `0x97` `DeleteAutomationHubCommand` was described in
one pass as "undocumented anywhere in the tree". That is true of the decompiled
source tree, but **this repo already documented and implemented it** — see
`mesh_opcodes.md`'s hub family section. It is not a new opcode.
