# Hub command family — consolidated opcode table

> **Follow-up: the "no 7-byte routing prefix" result below is the highest-value
> item this pass produced.** cync-lan's shipped hub commands build their length
> field as `8 + len(payload)` (routing 7 + op 1 + payload), which this finding
> contradicts. Written up as a byte-level A/B test in the cync-lan repo at
> `docs/hub_envelope_ab_test.md`, with exact bytes for both candidate envelopes
> so a single hardware test settles it. Six shipped commands depend on the
> answer.
>
> Also correcting one claim below: `0x97` `DeleteAutomationHubCommand` is
> described as undocumented anywhere in the tree. True of *this* tree, but the
> cync-lan repo already documents and implements it — it is not a new opcode.

Scope: the 15 `*HubCommand` / hub-scoped classes in
`com/gelighting/cbygekit/services/devices/command/`. All paths in this document are relative to
`sources/` in the `cync_decompiled_v2` tree.

Evidence tiers used throughout: **confirmed** (cited class + the construct that proves it),
**plausible** (reasonable inference, says what would confirm it), **not found** (looked for,
could not establish).

---

## 1. The table

Every outer op_code below is **confirmed**: each is either a raw byte literal passed as the
`op_code` argument at the command's own send call, or a named `XlinkCommandCode` constant whose
byte value is set in `com/gelighting/cbygekit/services/devices/xlink/XlinkCommandCode.java`.
No value in this table is inferred.

| # | Command class | Outer op_code | Payload len | Framer | Response notification |
|---|---|---|---|---|---|
| 1 | `SearchDevicesHubCommand` | **0x06** | 5 | `XlinkTranslatorKt` | `HubDeviceSearchStatusNotification` |
| 2 | `StopSearchDevicesHubCommand` | **0x06** | 5 | `XlinkTranslatorKt` | `HubDeviceSearchStatusNotification` |
| 3 | `CreateSceneHubCommand` | **0x10** | 50 | `Frame.m14440a()` | `HubCreateSceneNotification` |
| 4 | `DeleteSceneHubCommand` | **0x1F** | 2 | `XlinkTranslatorKt` | none |
| 5 | `CreateGroupHubCommand` | **0x30** | 50 | `Frame.m14440a()` | `HubCreateGroupNotification` |
| 6 | `DeleteGroupHubCommand` | **0x32** | 2 | `XlinkTranslatorKt` | none |
| 7 | `QueryHubInfoCommand` | **0x4B** | 64 | `XlinkTranslatorKt` | `HubInfoNotification` |
| 8 | `StartHubFirmwareUpdatesCommand` | **0x4F** | 32 | `XlinkTranslatorKt` | `HubFirmwareUpdateStatusNotification` |
| 9 | `QueryHubDeviceListCommand` | **0x51** | 6 | delegate `g()` | `HubDeviceListNotification` |
| 10 | `CreateScheduleHubCommand` | **0x92** | 50 | `XlinkTranslatorKt` | `HubCreateScheduleNotification` |
| 11 | `ToggleAutomationHubCommand` | **0x93** | 52 | `XlinkTranslatorKt` | none |
| 12 | `DeleteScheduleHubCommand` | **0x94** | 2 | `XlinkTranslatorKt` | none |
| 13 | `AddAutomationHubCommand` | **0x95** | 11 | `XlinkTranslatorKt` | none |
| 14 | `DeleteAutomationHubCommand` | **0x97** | 6 | `XlinkTranslatorKt` | none |
| 15 | `MeshStatusProxyHeartbeatCommand` | **0xAF** | 2 | delegate `g()` | none |

"none" = the class extends `UnitDeviceCommand`, has no `StatusNotificationQueryCommand` type
parameter, and its op_code is absent from `XlinkNotificationParser`'s response `EnumMap`
(`com/gelighting/cbygekit/services/devices/xlink/XlinkNotificationParser.java:86-115`) — so an
incoming frame carrying that op_code has no registered parser at all. **Confirmed** as
"no response is parsed by the app"; whether the hub nevertheless emits one on the wire is **not
found**.

### Op_code name provenance

`XlinkCommandCode.java` carries a JADX `Failed to restore enum class` error. Constants that still
have a real static field keep their true dex field name; constants marked
`/* JADX INFO: Fake field, exist only in values array */` were reconstructed by JADX and their
**labels are unreliable** (three entries are labelled `HUB_WIFI_CONFIGURE`, two `HUB_PASSTHROUGH_8C`,
two `HUB_ADD_ACTION_TO_SCHEDULE`). The byte values are real either way, because they come from the
surviving constructor calls.

| Op | Enum label in this JADX run | Label trustworthy? |
|---|---|---|
| 0x06 | `HUB_SEARCH_DEVICES` | yes — real field |
| 0x10 | `HUB_CREATE_SCENE` (`Tnaf.POW_2_WIDTH`, = 16, `com/thingclips/bouncycastle/math/p055ec/Tnaf.java:10`) | yes — real field |
| 0x30 | `HUB_CREATE_GROUP` | yes — real field |
| 0x4B | `QUERY_HUB_INFO` | yes — real field |
| 0x4F | `HUB_ENSURE_UPDATE` | yes — real field |
| 0x51 | `QUERY_HUB_DEVICE_INFO_PAGES` | yes — real field |
| 0x92 | `HUB_CREATE_SCHEDULE` | yes — real field |
| 0x1F | `HUB_DELETE_SCENE` | no — fake field (value still confirmed) |
| 0x32 | `HUB_DELETE_GROUP` | no — fake field (plausible: 0x30/0x31/0x32 = create/edit/delete group) |
| 0x93 | `HUB_WIFI_CONFIGURE` (mislabel) | no — fake field |
| 0x94 | `HUB_PASSTHROUGH_8C` (mislabel) | no — fake field |
| 0x95 | `HUB_ADD_ACTION_TO_SCHEDULE` | no — fake field |
| 0x97 | `HUB_WIFI_CONFIGURE` (mislabel) | no — fake field |
| 0xAF | **absent from `XlinkCommandCode` entirely** | n/a — see #15 below |

This does not weaken any row of the main table: **every one of the 15 commands passes its op_code
as a raw byte literal or via a real-field constant at its own call site**, so the value never
depends on a reconstructed label.

---

## 2. Framing — three code paths, one wire format

**Confirmed.** All three paths converge on the identical byte layout, and all three end at
`XlinkCommandDelegate.mo14053e(byte[])` with a fully-formed frame.

```
0x7E │ msgId(4B LE) │ flag(1B) │ op_code(1B) │ len(2B LE) │ payload │ cksum(1B) │ 0x7E
     └──────────── byte-stuffed: 0x7E→{0x7D,0x5E}, 0x7D→{0x7D,0x5D} ────────────┘
```

* `len` = `payload.length` only (does **not** include op_code or checksum).
* `cksum` = sum of `op_code ‖ len(2B) ‖ payload`, mod 256. The msgId and flag bytes are **not**
  in the checksum.
* The two `0x7E` delimiters are written raw, after stuffing.

### Path A — `XlinkTranslatorKt.m14449a(msgId, op, WriteBuffer)`
`com/gelighting/cbygekit/services/devices/xlink/legacy/XlinkTranslatorKt.java` is a one-line
wrapper over `Xlink.m14391a(op, data, msgId)`
(`com/gelighting/cbygekit/services/devices/xlink/Xlink.java`). Flag byte is hardcoded
`pdqbbbp.dpdqppp` = 248 = **0xF8**. Used by 11 of the 15 commands.

### Path B — `new Frame(msgId, Direction.REQ, XlinkCommandCode, Packable).m14440a()`
`com/gelighting/cbygekit/services/devices/xlink/legacy/Frame.java`. Used only by
`CreateSceneHubCommand` and `CreateGroupHubCommand`.

**Confirmed byte-identical to Path A for these call sites**, re-verified this pass:
* `Frame.Direction.REQ` = `(byte) -8` = **0xF8** — the same value `Xlink.m14391a()` hardcodes.
  (`RSP` = 0xF9, `ANNOUNCE` = 0xFA exist but no command sends them.)
* Same field order, same LITTLE_ENDIAN msgId and len.
* Same checksum domain (`op ‖ len ‖ payload`). `Frame` accumulates `(i + b) % 256` per byte where
  `Xlink` sums then takes `% 256` once — congruent mod 256, so the final `(byte)` cast is equal.
* Same escape rule (`Frame.m14439b`), applied per byte; `Frame` escapes header, body and checksum
  as three chunks, `Xlink` escapes the concatenation — same output, since escaping is per-byte.

So **the Frame-vs-XlinkTranslator distinction changes nothing on the wire.** It is two encoder
classes emitting the same bytes. This confirms the earlier note on `CreateSceneHubCommand.java`.

### Path C — `XlinkCommandDelegate.DefaultImpls.m14393b(delegate, op, payload)`
Used by `QueryHubDeviceListCommand` and `MeshStatusProxyHeartbeatCommand`.
`com/gelighting/cbygekit/services/devices/xlink/XlinkCommandDelegate.java` shows this forwards to
`mo14055g(op, payload, msgId)`, and
`com/gelighting/cbygekit/services/devices/xlink/XlinkDeviceManager.java:1005-1009` shows
`mo14055g` is `mo14053e(Xlink.m14391a(b, bArr, i))` — i.e. **the same `Xlink.m14391a()` framer**,
just reached through the delegate instead of pre-framed by the command.

**Important negative result (confirmed):** none of the 15 commands reaches `f()` / `mo14054f`,
which is the method that prepends the 7-byte routing prefix
(`msgId 3B LE ‖ 0x00 0x00 ‖ MeshAddress 2B LE`) documented at
`XlinkDeviceManager.java:984-1000`. **The Hub family carries no mesh-address routing prefix.** Its
payload starts immediately after `len`. Nor does any of them reach `h()` / `mo14056h`, so none is
affected by the 0x8E mesh-relay bug.

### Receive side
`XlinkNotificationParser.java:401-509` de-stuffs first (`0x7D 0x5E`→`0x7E`, `0x7D 0x5D`→`0x7D`),
rejects frames < 11 bytes, requires `0x7E` at both ends, then reads
`msgId = bytes[1:4] LE`, `flag = bytes[5]`, `op_code = bytes[6]`, `len = bytes[7:8] LE`,
`payload = bytes[9 .. len-3]`. The op_code byte is looked up in `XlinkCommandCode.f37551c`; unknown
codes are logged as `unknownControlCode` and dropped. **Responses reuse the same op_code as their
request** — the `EnumMap` at `XlinkNotificationParser.java:86-115` is keyed by the request's
`XlinkCommandCode`. Confirmed.

### Transport (new evidence — see §5)
`mo14053e` → `XlinkDeviceManager$postCommand$2` → `XlinkDeviceManager.java:1717`:
`xlinkAgentManager.m13598e().sendPipeData(xDevice, frameBytes, 7, listener)`.
`sources/io/xlink/wifi/sdk/XlinkAgent.java:1095-1121`: if the Xlink UDP service is connected and
the device `isLanControlDev()`, it goes out via `UdpSendPacket.sendPipe()` (LAN UDP direct to the
device); otherwise via `TcpSendPacket.sendPipe()` (Xlink cloud TCP).

---

## 3. Per-command detail

All multi-byte integers below are **little-endian**, confirmed at source:
* `WriteBuffer.m14444d(int)` writes 2 bytes LE, `m14443c(int)` writes 4 bytes LE
  (`com/gelighting/cbygekit/services/devices/xlink/legacy/WriteBuffer.java`).
* `PackKt.m14453a(...)` allocates `ByteBuffer.order(LITTLE_ENDIAN)`
  (`com/gelighting/cbygekit/services/devices/xlink/legacy/packet/PackKt.java`).
* `ExtensionsKt.m13359f(DataOutputStream, int)` writes 2 bytes LE
  (`com/gelighting/cbygekit/foundation/ExtensionsKt.java:81-85`).
* `String30.Companion.m14455a(s)` = UTF-8 bytes of `s`, `Arrays.copyOf(..., 30)` — truncated or
  zero-padded to **exactly 30 bytes**, no length prefix, no NUL guarantee if the name is exactly
  30 bytes (`.../legacy/packet/String30.java`).
* A `WriteBuffer(n)` is zero-filled at construction, so every byte the command does not write is
  `0x00`. All "reserved/zero" fields below follow from that, and are **confirmed**, not assumed.

---

### 1. `SearchDevicesHubCommand` — op **0x06**
`command/SearchDevicesHubCommand.java:89-97`. `WriteBuffer(5)`; only 4 bytes written.

| Off | Size | Field |
|---|---|---|
| 0 | 2 LE | `durationSeconds` (default 30; `Companion.DEFAULT_DURATION_SECONDS`) |
| 2 | 2 LE | `remainingSeconds` (constructor seeds it equal to duration) |
| 4 | 1 | 0x00 (never written) |

Response: `HubDeviceSearchStatusNotification` (registered for `HUB_SEARCH_DEVICES`,
`XlinkNotificationParser.java:101`). Layout (`.../notification/HubDeviceSearchStatusNotification.java`):
**1 byte** at offset 0 — `0x00` → `hasNewDevices = false`, anything else → `true`. Nothing else read.

### 2. `StopSearchDevicesHubCommand` — op **0x06**
`command/StopSearchDevicesHubCommand.java:47-55`. Same op, same 5-byte shape, both u16 fields
written as `0` — i.e. **stop = search with duration 0**. Confirmed. Response class identical to #1.

### 3. `CreateSceneHubCommand` — op **0x10**
`command/CreateSceneHubCommand.java:139-143`. Path B (`Frame`), `XlinkCommandCode.HUB_CREATE_SCENE`.
Payload is `CreateSceneRequest.getF38055a()` =
`PackKt.m14453a(String30 bytes, Short iconId, new byte[18])` → **50 bytes**:

| Off | Size | Field |
|---|---|---|
| 0 | 30 | scene name, UTF-8, zero-padded/truncated to 30 |
| 30 | 2 LE | `iconId` — **hardcoded `(short) 0`** in the constructor; the app never sets it |
| 32 | 18 | zero padding |

Response: `HubCreateSceneNotification` (`XlinkNotificationParser.java:94`), 3 bytes of payload read:

| Off | Size | Field |
|---|---|---|
| 0 | 1 | `errorCode` (signed byte, stored as-is) |
| 1 | 2 LE | allocated `sceneId`, `getShort()` then `if (i<0) i += 65536` → u16 |

### 4. `DeleteSceneHubCommand` — op **0x1F**
`command/DeleteSceneHubCommand.java:82-89`. Raw literal `(byte) 31`. `WriteBuffer(2)`:

| Off | Size | Field |
|---|---|---|
| 0 | 2 LE | `sceneId` (`SceneId.f41457b`, an `Int`) |

`MeshAddress` is accepted as a parameter and unused — hub-scoped, not per-device. No response class.

### 5. `CreateGroupHubCommand` — op **0x30**
`command/CreateGroupHubCommand.java:96-100`. Path B, `XlinkCommandCode.HUB_CREATE_GROUP`.
`CreateGroupRequest` is byte-for-byte the same shape as `CreateSceneRequest` — 30-byte name +
2-byte LE `iconId` (hardcoded 0) + 18 zero bytes = **50 bytes**.

Response: `HubCreateGroupNotification` (`XlinkNotificationParser.java:93`). Layout differs
slightly from the scene/schedule pair — the id is only read when the error byte is zero:

| Off | Size | Field |
|---|---|---|
| 0 | 1 | `errorCode` |
| 1 | 2 LE | allocated `groupId`, u16 — **read only if `errorCode == 0`**, otherwise `null` |

### 6. `DeleteGroupHubCommand` — op **0x32**
`command/DeleteGroupHubCommand.java:62-69`. Raw literal `(byte) 50`. `WriteBuffer(2)`:

| Off | Size | Field |
|---|---|---|
| 0 | 2 LE | group `MeshAddress` (`f31064a & 0xFFFF`) |

Note this is a *mesh address*, not the `groupId` the create-response returns. No response class.

### 7. `QueryHubInfoCommand` — op **0x4B**
`command/QueryHubInfoCommand.java:41-47`. `WriteBuffer(64)` with **nothing written** — 64 zero
bytes. Confirmed.

Response: `HubInfoNotification` (`XlinkNotificationParser.java:91`). Four fixed 16-byte
NUL-terminated UTF-8 strings (`XlinkPacketsKt.m14446b(buffer, 16)`, which truncates at the first
`0x00`), read from a **big-endian-agnostic** `ByteBuffer.wrap` (no numeric fields, so byte order
is irrelevant):

| Off | Size | Field |
|---|---|---|
| 0 | 16 | firmware version, part 1 |
| 16 | 16 | firmware version, part 2 |
| 32 | 16 | MAC address string → `MacAddress.Companion.m13561a(...)` |
| 48 | 16 | setup code |

`firmwareVersion` is presented to the app as `part1 + "." + part2`. 64 bytes total — matching the
64-byte request payload.

### 8. `StartHubFirmwareUpdatesCommand` — op **0x4F**
`command/StartHubFirmwareUpdatesCommand.java:117-125`. `WriteBuffer(32)`, 3 bytes written:

| Off | Size | Field |
|---|---|---|
| 0 | 1 | `0x00` (literal; `Companion` declares `ACTION_CANCEL`/`ACTION_CONFIRM` — which of the two is `0x00` is **not found**, only `0x00` is ever emitted) |
| 1 | 2 | `{0x00,0x00}` when the ctor flag (`toString` names it `hubOnly`) is true, else `{0xFF,0xFF}` |
| 3 | 29 | zero padding |

**Plausible** reading of offset 1-2: it is a target mesh address, `0x0000` = the hub itself,
`0xFFFF` = broadcast to all mesh devices. The `Companion` declares `ENSURE_HUB_UPGRADE` and
`ENSURE_DEVICE_UPGRADE` as the two byte arrays, which fits; but `@Metadata` source order would map
them the other way round, and source order is only a hint, so **the name-to-value binding is not
found**. The byte values and the `hubOnly` branch are confirmed.

Response: `HubFirmwareUpdateStatusNotification` (`XlinkNotificationParser.java:100`). Parser
returns `null` if the payload is 1 byte. Otherwise, reading forward from offset 0:

| Off | Size | Field |
|---|---|---|
| 0 | 1 | status code → `HubUpdateStatusType`: `-1` UNKNOWN, `0` NONE, `1` CHECK_IN_PROGRESS, `2` DOWNLOADING, `3` UPGRADING, `4` FAILED. Bytes `50`/`51` (0x32/0x33) are special-cased to UNKNOWN. Unknown values throw. |
| 1 | 1 | skipped |
| 2 | 1 | progress percent (used by DOWNLOADING/UPGRADING) |
| 3 | 1 | skipped |
| 4 | 2 LE | device `MeshAddress` (`& 0xFF`); `0` → the hub's own broadcast address |
| 6 | 10 | NUL-terminated string, parsed then **discarded** |
| 16 | 10 | NUL-terminated string → the version reported with DOWNLOADING/UPGRADING |
| 26 | 4 LE | read then **discarded** |

Enum codes from `.../notification/HubUpdateStatusType.java`; field offsets from
`.../notification/HubFirmwareUpdateStatusNotification.java` (`ReadBuffer` reads sequentially).

### 9. `QueryHubDeviceListCommand` — op **0x51**
`command/QueryHubDeviceListCommand.java:57-68`. **Path C** — `DefaultImpls.m14393b(..., (byte) 81, ...)`.
The class's `@Metadata` declares a `Companion` constant literally named `OPCODE`, corroborating
that `81` is the opcode and not a payload byte. Payload = 6 bytes via `ExtensionsKt.m13359f`:

| Off | Size | Field |
|---|---|---|
| 0 | 2 LE | `0x0000` (literal) |
| 2 | 2 LE | `total` (default `-1` → `0xFFFF`; ctor rejects `0` and anything outside `-1..32767`) |
| 4 | 2 LE | `offset` (default 0; ctor requires `0..32767`) |

Response: `HubDeviceListNotification` (`XlinkNotificationParser.java:92`).

| Off | Size | Field |
|---|---|---|
| 0 | 4 | not read by the parser (**not found** what these carry) |
| 4 | 2 LE | record count (`DataBytes.m15001d` = LE u16) |
| 6 | 53×N | device records |

Each 53-byte record (`.../model/HubDeviceInfo.java` `Parser`, LITTLE_ENDIAN buffer):

| Off | Size | Field |
|---|---|---|
| +0 | 2 LE | mesh address (u16; only the low byte is used to build the `MeshAddress`) |
| +2 | 1 | device type code (`0` → null) |
| +3 | 30 | device name, NUL-terminated UTF-8 |
| +33 | 2 | read and **discarded** |
| +35 | 12 | firmware/version string, NUL-terminated UTF-8 |
| +47 | 6 | MAC, raw bytes rendered as hex (byte order is reversed for non-`ProductType.f31197O` products) |

### 10. `CreateScheduleHubCommand` — op **0x92**
`command/CreateScheduleHubCommand.java:147-159`. Raw literal `(byte) -110`. `WriteBuffer(50)`:

| Off | Size | Field |
|---|---|---|
| 0 | 4 LE | `sceneId` (`m14443c`) — note **4 bytes**, unlike every other id field in this family |
| 4 | 26 | zero (cursor is force-set to 30) |
| 30 | 2 LE | `0x0000` |
| 32 | 1 | `enabled` (0/1) |
| 33 | 1 | `0x00` |
| 34 | 16 | zero padding |

This command is a bare **schedule-ID allocator** — it carries no name, no day-of-week and no time.
Confirmed by `services/scenes/RoutinesService.java:1647`, whose whole purpose is to obtain an id
from the response before building the real trigger command.

Response: `HubCreateScheduleNotification` (`XlinkNotificationParser.java:95`). Byte-for-byte the
same parser as `HubCreateSceneNotification`:

| Off | Size | Field |
|---|---|---|
| 0 | 1 | `errorCode` |
| 1 | 2 LE | allocated `scheduleId`, u16 |

### 11. `ToggleAutomationHubCommand` — op **0x93**
`command/ToggleAutomationHubCommand.java:116-129`. Raw literal `(byte) -109`. `WriteBuffer(52)`:

| Off | Size | Field |
|---|---|---|
| 0 | 2 LE | `scheduleId` |
| 2 | 4 LE | `sceneId` (again 4 bytes) |
| 6 | 26 | zero (cursor force-set to 32) |
| 32 | 2 LE | `0x0000` |
| 34 | 1 | `enable` (0/1) |
| 35 | 1 | `0x00` |
| 36 | 16 | zero padding |

Same tail shape as #10, offset by the 2-byte leading `scheduleId`. No response class.
Dispatched from `services/scenes/RoutinesService.java:652`.

### 12. `DeleteScheduleHubCommand` — op **0x94**
`command/DeleteScheduleHubCommand.java:77-84`. Raw literal `(byte) -108`. `WriteBuffer(2)`:

| Off | Size | Field |
|---|---|---|
| 0 | 2 LE | `scheduleId` |

Dispatched from `RoutinesService.java:1259` (`deleteScheduleFromHubInternal`). No response class.

### 13. `AddAutomationHubCommand` — op **0x95**
`command/AddAutomationHubCommand.java:111-164`. Raw literal `(byte) -107`. `WriteBuffer(11)`,
fully written:

| Off | Size | Field |
|---|---|---|
| 0 | 2 LE | `scheduleId` (`ScheduleModel.f41890p`) |
| 2 | 2 LE | `sceneId` |
| 4 | 1 | day-of-week bitmask: Sun `0x01`, Mon `0x02`, Tue `0x04`, Wed `0x08`, Thu `0x10`, Fri `0x20`, Sat `0x40` (bit 7 unused) |
| 5 | 4 LE | trigger time — see below |
| 9 | 2 LE | `sceneId` **again** |

Offset 5-8 is `writeBuffer.m14443c(number.intValue())` where `number` is:
* `ScheduleTime` is `null` → `0`
* `ScheduleTime.Local` → `localDateTime.toEpochSecond(ZoneOffset.UTC)` boxed as a `Long`, then
  `.intValue()` — i.e. **the low 32 bits of a UTC epoch-second value**, written LE
* `ScheduleTime.Sunrise` → `(byte) -15` = **0xF1**, then `.intValue()` = `-15` → written as the
  int32 `FF FF FF F1` reversed, i.e. bytes `F1 FF FF FF`
* `ScheduleTime.Sunset` → `(byte) -16` = **0xF0** → bytes `F0 FF FF FF`

The `Companion` declares exactly two constants, `CODE_SUNRISE` and `CODE_SUNSET`, matching those
two sentinels. **Note:** `ScheduleTime.Sunrise`/`Sunset` each carry an `offset: Int`
(`services/schedules/ScheduleTime.java`), and this Hub command **discards it** — only the sentinel
is sent. Confirmed. (The BLE sibling `AddAutomationCommand` does encode the offset.)

No response class. Dispatched from `RoutinesService.java:496`, immediately after
`CreateScheduleHubCommand` (#10) has returned an id.

### 14. `DeleteAutomationHubCommand` — op **0x97**
`command/DeleteAutomationHubCommand.java:66-73`. Raw literal `(byte) -105`. `WriteBuffer(6)`, only
2 bytes written:

| Off | Size | Field |
|---|---|---|
| 0 | 2 LE | `scheduleId` |
| 2 | 4 | zero (never written) |

**Not previously documented anywhere in this tree.** Dispatched from `RoutinesService.java:1076`
(`deleteScheduleFromDevices`, hub branch), which is the counterpart of the `AddAutomationHubCommand`
call at `:496`. This makes the schedule model symmetric and confirms the two-concept split:

* **Schedule slot**: create `0x92` / delete `0x94`
* **Automation trigger data**: add `0x95` / delete `0x97` / toggle `0x93`

Why the buffer is 6 bytes when only 2 are used is **not found** — plausibly the payload is meant
to mirror `ToggleAutomationHubCommand`'s `scheduleId`+`sceneId` head, with the `sceneId` left zero.
No response class.

### 15. `MeshStatusProxyHeartbeatCommand` — op **0xAF**
`command/MeshStatusProxyHeartbeatCommand.java:61-64`. **Path C**:
`DefaultImpls.m14393b(delegate, (byte) -81, new byte[]{-81, enable}, ...)`.

| Off | Size | Field |
|---|---|---|
| 0 | 1 | `0xAF` — the op_code **repeated inside the payload** |
| 1 | 1 | `enable` (0/1) |

The class's `@Metadata` declares a `Companion` constant named `OPCODE`, corroborating `0xAF`.
This is the only command in the family whose op_code has **no entry at all** in
`XlinkCommandCode` — so per `XlinkNotificationParser.java:497-506` any inbound `0xAF` frame would
be dropped as `unknownControlCode`. Consistent with it being fire-and-forget. It is also the only
command in the family whose payload duplicates its own op byte.

---

## 4. Cross-cutting facts

**All 15 declare `ConnectionType = {WIFI}` only.** Each sets its supported-connection set to
`DeviceCommand.f34462l` or `f34463m`, and `command/DeviceCommand.java:183-188` shows both are
`EnumSet.of(ConnectionType.f35865d)`, which `cyncdec enums ConnectionType` resolves to **`WIFI`**.
`controller/DeviceController.java:37-40` rejects any command whose set does not contain the
controller's own `ConnectionType`, and `controller/AbstractXlinkDeviceController.java:66` sets that
to `WIFI`. **Confirmed: no hub command in this family can be dispatched over `BLE`, `BLE_PROXY`, or
`WIFI_PROXY`.**

**None overrides the BLE send path.** No class in the family implements `mo14012f`/`mo14060M`
(the `TelinkCommandDelegate` path) — grep-verified across all 15 files. The Xlink path is the only
path.

**Request/response correlation** (confirmed, cited by the existing note on
`command/StatusNotificationQueryCommand.java:52-88` and re-verified against
`XlinkNotificationParser.java:86-115, 497-514`): op_code selects which notification class is
constructed at all; the 4-byte msgId echoed back in the response frame must equal the request's
msgId. There is no sequence number or queue.

---

## 5. Earlier claims: confirmed, unconfirmed, contradicted

### Confirmed (re-derived independently this pass)
1. `CreateScheduleHubCommand` (0x92) allocates a bare schedule ID and carries no trigger data —
   confirmed by both the payload and `RoutinesService.java:1647`.
2. `AddAutomationHubCommand` = op **0x95**, 11-byte payload carrying day-of-week / time / sceneId —
   confirmed field by field.
3. `CreateSceneHubCommand` / `CreateGroupHubCommand` use `Frame.m14440a()`, and it builds
   byte-for-byte the same frame as `Xlink.m14391a()` — re-verified in detail (§2 Path B), including
   the checksum-domain and escape-chunking equivalences that make the two encoders equal rather
   than merely similar.
4. Delete/Toggle Hub commands use `XlinkTranslatorKt` and never `Frame` — re-verified.
5. `HubCreateSceneNotification`: op 0x10, 3-byte response `errorCode(1) ‖ sceneId(u16 LE)` —
   confirmed, and the identical layout confirmed for `HubCreateScheduleNotification`.
6. Op values 0x1F, 0x92, 0x93, 0x94 as recorded in the existing `XlinkCommandCode.java` note.

### New evidence found this pass
7. **`DeleteAutomationHubCommand` = op 0x97** (6-byte buffer, `scheduleId` u16 LE at offset 0,
   4 trailing zero bytes). Not documented anywhere in this tree before. It is the delete counterpart
   to `AddAutomationHubCommand` (0x95), dispatched from `RoutinesService.java:1076`.
8. **The Hub family carries no 7-byte routing prefix.** All 15 bypass `f()`/`mo14054f`. This
   matters for anyone reimplementing: the payload sits directly after the 2-byte length field.
9. **All 15 declare `ConnectionType = {WIFI}` exclusively**, and the controller layer hard-rejects
   a mismatch (§4). This is a genuine narrowing of the long-standing open question.
10. **The actual network write is `XlinkAgent.sendPipeData()`** — `XlinkDeviceManager.java:1717`
    → `io/xlink/wifi/sdk/XlinkAgent.java:1095-1121`, which routes to `UdpSendPacket.sendPipe()`
    (LAN UDP, direct to the device) when the Xlink UDP service is up and the device is
    `isLanControlDev()`, otherwise to `TcpSendPacket.sendPipe()` (Xlink cloud TCP). The earlier
    notes explicitly said `mo14053e()`'s network-write path "was not traced here".

### Still unconfirmed — the ceiling is unchanged
The transport question is **narrowed but not closed**, and the earlier
"plausible, not independently confirmed" rating still stands for the Create/Delete/Toggle family.
Finding 9 rules out BLE-GATT for this family — that half of the old open question is now
**answered: not BLE**. Finding 10 does **not** promote the rest, for a specific reason worth
stating plainly:

> This is the **phone→device / phone→cloud** direction. cync-lan's relay intercepts the
> **device→cloud** TCP connection. Even where `sendPipeData` takes the LAN UDP branch, that is the
> phone talking UDP straight to the hub — a different socket from the one cync-lan sits on. Whether
> the bytes cync-lan's relay can inject on the device's TCP connection are framed this way is
> **not established** by anything read here.

So: do not ship these frames into cync-lan's TCP `PacketBuilder` as if the framing were confirmed
for that socket. The op_codes and payload layouts (§1, §3) are solid and transport-independent;
the HDLC/PPP envelope around them is what remains unproven for cync-lan's own wire.

### Contradicted / corrected
11. **The note on `CreateScheduleHubCommand.java` describes `AddAutomationHubCommand`'s time field
    as "an epoch-second value for `ScheduleTime.Local` (LocalDateTime built against a fixed dummy
    date so this really only encodes seconds-since-midnight, not a real date)" and the sunrise/sunset
    case as "a signed sunrise/sunset offset byte (0-15/-0x10 sentinel)". Both halves need
    correcting:**
    * The sunrise/sunset case sends **no offset at all**. `AddAutomationHubCommand.java:151-158`
      boxes a bare `(byte) -15` (sunrise) or `(byte) -16` (sunset); `ScheduleTime.Sunrise`/`Sunset`
      each carry an `offset: Int` field that this command never reads. Confirmed by reading both
      classes. The `0-15` phrasing in the old note also mis-renders `-15`.
    * The "fixed dummy date" characterisation is **not found** — I could not locate any construction
      site that pins `ScheduleTime.Local` to a constant date. The one `ScheduleTime.Local`
      construction inside ge-sdk that I traced,
      `services/schedules/ScheduleServiceDefault.java:6954-6962`, uses `LocalDate.now()` with a
      `LocalTime.of(h,m,s)` — a *current* date, and it sits in a read-back path
      (`mo14850B(DeviceId, …)`), not the create path. The transport-independent fact, which **is**
      confirmed, is simply: the wire value is the low 32 bits of
      `localDateTime.toEpochSecond(ZoneOffset.UTC)`, little-endian. Whether the date component is
      meaningful to the hub depends on the caller and is **not found**. An implementer should not
      assume seconds-since-midnight.
12. Minor: the existing note on `Xlink.java` describes `Xlink.a()`'s `data` argument as "the 7-byte
    routing prefix + commandBody". That is true for commands reaching it via `f()`, but **false for
    every command in this family** — they call `g()` / `XlinkTranslatorKt` with a bare payload. Not a
    contradiction of the note's own scope, but the phrasing is misleading if applied to Hub commands.
