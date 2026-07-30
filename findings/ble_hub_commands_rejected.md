# Hub commands over BLE — hypothesis rejected, and the tests were malformed

**Status: the `0xEA` packet was background noise. Separately, all four runs sent
malformed requests, so hub-over-BLE was never actually tested.**

Kept rather than deleted, because how this went wrong is more instructive than
the result.

## What was claimed

A hub query (`QueryHubInfoCommand`, `0x4B`) was sent over BLE and an unexplained
notification arrived:

```
a87b750b00b031ea11020b83031c044100000000
```

`0xEA` (`BASE_EA`), vendor `0x11 0x02` correct, subtype `0x83` — undocumented. It
was the first non-`0xDC` notification seen on this transport, and a control run
sending nothing produced none. That looked like evidence.

## It did not reproduce

| run | sent | `0xEA` |
|---|---|---|
| 1 | `0x4B` QueryHubInfo | **yes** — subtype `0x83` |
| 2 | nothing | no |
| 3 | `0x46` QueryDeviceTime | no |
| 4 | `0x4B` **repeated, identical** | **no** |

Run 4 is decisive: same node, same opcode, same ordering, same duration, and
nothing. One unexplained packet across four runs on a mesh with forty chattering
nodes is background traffic. **Treat `0xEA`/`0x83` as unrelated.**

## The tests were malformed anyway

This is the part worth keeping. Reading `devices.py:_query_hub` — which should
have happened *before* designing the test rather than after it failed — shows
three things that make all four runs meaningless as tests of hub-over-BLE:

```python
payload = bytes(buffer_len)               # 64 zero bytes, not empty
await broadcast_control_command(
    op, cmd_, 0x00, 0x00, payload, ...    # broadcast, not a device id
)
response = await _await_xlink_notification(op, ...)   # xlink, not mesh
```

1. **Addressing.** Hub commands are **broadcast** (`0x00`). Every run addressed
   mesh id 37, a wired switch.
2. **Payload.** The request is `buffer_len` zero bytes — 64 for `0x4B` — and the
   source is explicit that "the allocation size is the wire size, since the
   buffer is sent whole". These runs sent an *empty* payload: a different message.
3. **Reply channel.** The response is awaited as an **xlink** notification, a
   different channel from the mesh `0xDC`/`0xEA` notifications this probe reads.
   Even a correct request might have answered where nobody was listening.

## The structural point this exposes

A 64-byte payload **cannot fit in a 20-byte mesh packet**. `build_command` caps
the payload at 10 bytes, and that is not arbitrary — the Telink mesh command is
20 bytes.

So hub commands are not mesh commands with a large payload. They are a different
message class, and "do hub commands work over BLE" cannot be asked by putting a
hub opcode into a mesh packet. It needs segmentation, or a different
characteristic — `...1913` (read, write-without-response) is present on these
devices and its purpose is unknown — or hub commands may have no BLE expression
at all.

That reframes the earlier TCP work too. `hub_envelope_ab_test.md` concluded that
neither candidate envelope gets a reply. Combined with this, a hub command may
not be a *mesh* command in either transport — in which case the envelope search
was looking one layer too low.

## What would actually test it

Not another opcode. In order:

1. **Establish where a hub command can be carried at all.** 64 bytes needs
   segmentation or another channel; find which the app uses, from the decompile,
   before sending anything.
2. **Find which device holds the hub role**, since these are broadcast on TCP yet
   something must answer.
3. Only then construct a BLE request, and listen on whatever channel the reply is
   meant to use.

## How this went wrong

Recorded plainly. A hypothesis was formed from a single packet, a control run was
treated as near-confirmation, and three further runs were spent before anyone
read the twenty lines of `_query_hub` that showed the request had been malformed
from the first attempt. That reading was cheap and available the whole time.

The confidence markers in this project exist for exactly this. The packet looked
like a reply, and looking like one is not evidence.
