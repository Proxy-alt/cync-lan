# An unexplained `0xEA` notification, possibly a hub reply over BLE

**Status: one observation. Not established. A control run decides it.**

Written down before it is confirmed, because the temptation to call it a result
is exactly what has gone wrong repeatedly on this transport.

## What was seen

A hub query — `QueryHubInfoCommand`, opcode `0x4B`, empty payload — was sent over
BLE to mesh id 37, on a healthy link, and notifications were subscribed to
immediately afterwards. Among the packets that arrived before the link dropped:

```
a87b750b00b031ea11020b83031c044100000000
```

| field | byte | value |
|---|---|---|
| opcode | `[7]` | `0xEA` — `BASE_EA`, per `TelinkBaseNotificationParser` |
| vendor | `[8:10]` | `11 02` ✓ (the app's parser rejects anything else) |
| mode/flags | `[10]` | `0x0B` |
| **subtype** | `[11]` | **`0x83`** |
| payload | `[12:]` | `03 1c 04 41 00 00 00 00` |

## Why it is interesting

- **It is the first non-`0xDC` notification observed on this transport.** Every
  prior run — none of which sent a hub command — produced only mesh status.
- It is correctly framed. `TelinkNotificationParser` rejects any notification
  whose bytes 8–9 are not `0x11 0x02`; this one passes, so it is a real
  notification and not mis-decrypted noise.
- Subtype `0x83` is **not in the documented sub-dispatch table**
  (`findings/query_commands.md` §2.2), which lists `0x81`, `0x82`, `0x85`–`0x89`,
  `0xC0`, `0xC5`–`0xC7`, `0xD0`, `0xEE`, `0xF4`.
- By the documented `| 0x80` rule — response subtype equals request selector with
  the top bit set — `0x83` implies a request selector of `0x03`, also
  undocumented.

If this is a reply to `0x4B`, then **the hub command family works over BLE**, and
the family that has never answered over TCP becomes reachable on a transport
needing no DNS redirection. That would unlock scenes, schedules, automations, and
`QueryHubMeshNameAndPasswordCommand` — the query behind the integration's dead
`query_mesh_credentials` button.

## Why it is not established

- **One observation, one run.** No repeat.
- The request carried **no selector byte** — an empty payload — so the `| 0x80`
  rule does not connect `0x4B` to `0x83` in any obvious way. `0x4B` is a hub
  opcode, not a member of the `0xEA` query family at all.
- Nothing rules out unrelated background traffic. Forty-odd nodes are chattering,
  and a device settings or firmware notification arriving unprompted would look
  much like this.

## The control run — done, and it came back clean

Same node, same ordering, same `enable-first`, ten seconds, **nothing sent**.
Sixteen `0xDC` status packets arrived and **no `0xEA` of any subtype**.

| run | hub query sent | `0xEA` seen |
|---|---|---|
| 1 | `0x4B` QueryHubInfo | **yes** — subtype `0x83` |
| 2 | none | no |

That is the control this finding asked for, and it passed. The hypothesis is now
supported rather than merely suggested — but it is still one run each way, and
the coincidence it rules out is only the most obvious one.

## A second query produced nothing — the hypothesis is weaker

`0x46` QueryDeviceTime, sent on a healthy link with the same ordering, produced
**no `0xEA` at all**.

| run | sent | `0xEA` seen |
|---|---|---|
| 1 | `0x4B` QueryHubInfo | **yes** — subtype `0x83` |
| 2 | nothing | no |
| 3 | `0x46` QueryDeviceTime | **no** |

One positive against two negatives. Readings, in order of how much they would
cost to be wrong about:

1. **Run 1 was coincidence.** A single unexplained packet in one ten-second
   window, on a mesh with forty chattering nodes. The control saw none, but that
   is one sample against one sample.
2. **`0x4B` is answered and `0x46` is not.** Entirely possible — the device may
   not implement QueryDeviceTime, or it may need a payload rather than an empty
   one.
3. **Both were mis-addressed.** This is the confound that was missed at the
   start: both went to mesh id 37, a wired switch. If hub commands must be
   addressed to whichever device actually holds the hub role, neither test asked
   the right device anything, and run 1's packet was something else entirely.

## What to do next, and what not to

**Repeat `0x4B` exactly.** Not a new query — reproducibility of the original is
now the highest-information run available. If it comes back, `0x46` simply is
not supported and the finding stands. If it does not, run 1 was noise and this
document should say so.

Trying further queries before that would be looking for a positive rather than
testing the claim.

## The earlier plan, kept for the record

Not another `0x4B`. Send a **different** read-only hub query and see whether a
**different** subtype comes back:

| opcode | command | subtype if the `\| 0x80` rule holds |
|---|---|---|
| `0x46` | QueryDeviceTime | ? |
| `0xAD` | QuerySolConfig | ? |
| `0x8A` | QueryHubMeshNameAndPassword | ? |

One request producing one subtype could be coincidence. Two different requests
producing two different subtypes could not be. That is the experiment worth
running next, and it is as safe as the first — all three are reads.

## The original control instructions


Same ordering, same duration, **nothing sent**. If `0xEA`/`0x83` appears anyway
it is background traffic; if it does not, that is real evidence.

```bash
python probes/ble_control_probe.py --from-config /cfg/cync_mesh.yaml \
    --mesh-name <MESH> --mac <NODE> --notify-mode enable-first --listen 10
```

Then repeat the hub query a second time to check it reproduces. Two runs with a
reply and two without would settle it.

Worth trying afterwards if it holds up: the other safe read-only hub queries,
`0x46` QueryDeviceTime, `0xAD` QuerySolConfig, and `0x8A`
QueryHubMeshNameAndPassword. Different subtypes coming back for different
requests would be conclusive.

## Guessing at the payload, explicitly as a guess

`03 1c 04 41` could be a four-part version number (3.28.4.65), which would suit a
hub-info or firmware reply. Could equally be two 16-bit fields (`0x031C` = 796,
`0x0441` = 1089). **not found:** anything in the decompile that names subtype
`0x83` or selector `0x03`.
