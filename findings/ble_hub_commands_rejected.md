# Hub commands over BLE — closed. Confirmed unsupported by the real app's own code.

**Status: the `0xEA` packet was background noise, the tests that produced it were
malformed, and — settling the question for good — the decompiled app explicitly
refuses to send these commands over BLE at all. This is not a gap in what has
been tested. It is how the real client is written.**

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

## Confirmed from the decompile: it is gated in code, not merely untested

Reading the command classes (`services/devices/command/`) and the dispatch layer
answers this directly, rather than by more probing.

Every `DeviceCommand<T>` declares `mo14014o(): Set<ConnectionType>` — which
transports it supports, from `{ROUTING, BLE, BLE_PROXY, WIFI, WIFI_PROXY}`. For
every hub command relevant here:

| command | declared transports |
|---|---|
| `QueryHubInfoCommand` | `{WIFI}` |
| `QueryHubMeshNameAndPasswordCommand` | `{WIFI}` |
| `DeleteAutomationHubCommand` | `{WIFI}` |
| `StartHubFirmwareUpdatesCommand` | `{WIFI}` |
| `QuerySolConfigCommand` | `{ROUTING, WIFI}` |

**`BLE` appears in none of them.** None override the BLE send method
(`mo14012f`/`mo14060M`); the base class default is to
`throw new UnsupportedOperationException()`. The response side matches — each
notification class (`HubInfoNotification`, `HubMeshNameAndPasswordNotification`,
`SolConfigNotification`) declares only an `XlinkParser`, no Telink/BLE parser at
all.

The gate is enforced structurally, before any GATT write is possible —
`DeviceController.DefaultImpls.m14176a`:

```java
if (!CollectionsKt.contains(command.mo14014o(), deviceController.mo14150j())) {
    return new Err(new UnsupportedDeviceCommandException(...));
}
```

`TelinkBleDeviceController` is hardcoded to `ConnectionType.BLE`. Since none of
these commands list `BLE` in their supported set, this check fails and the
command never reaches the Telink command delegate — never becomes a mesh
packet, never needs chunking. **This is what "no BLE expression" looks like in
the actual client**, not an absence of evidence.

One instructive exception: `QueryDeviceTimeCommand` *does* support
`{BLE, BLE_PROXY, WIFI, WIFI_PROXY}` — but over BLE it sends a **different,
small** payload (a 4-byte opcode), not a BLE-segmented version of the 64-byte
Xlink buffer. So "supported over multiple transports" does not mean "same wire
bytes on each" — worth remembering before assuming any other multi-transport
command behaves identically across them.

No general large-payload BLE mechanism was missed, either: `sendBlocks` (used by
11 non-hub commands — light shows, music shows, tile layouts, multi-colour
bitmaps) and the separate OTA chunking path (`writeFirmwareDataChunk`, over
characteristic `...1913` — confirmed as the firmware-update channel, unrelated to
notifications) are the only two chunking schemes in the app. Neither is ever
invoked by a hub command.

## What this settles

Combined with the earlier `hub_envelope_ab_test.md` result (neither TCP envelope
gets a reply either), the picture is now complete rather than merely negative:
hub commands are Xlink/WIFI-only **by design in the real client**. Not "unproven
over BLE" — excluded from it in the source. There is no envelope, segmentation
scheme, or addressing fix that unlocks this; the real app itself has no code
path that would send one.

## How this went wrong

Recorded plainly. A hypothesis was formed from a single packet, a control run was
treated as near-confirmation, and three further runs were spent before anyone
read the twenty lines of `_query_hub` that showed the request had been malformed
from the first attempt. That reading was cheap and available the whole time.

The confidence markers in this project exist for exactly this. The packet looked
like a reply, and looking like one is not evidence.
