# `Query*` commands — opcodes, envelopes, and response types

> **UPDATE (cross-referenced after this file was written): the multipart
> reassembly flagged below as untraced is substantially answered in
> [`multipart_commands.md`](multipart_commands.md) — see its "Receive-side
> mirror" section.** The chunk header is `dataBytes[12]` = 1-based sequence,
> and on the first packet only, `dataBytes[13]` = total part count with the
> body starting at `[14]`. That pass and this one ran concurrently and could
> not see each other's output. What remains open is narrower than stated here:
> whether the music-show and motion-sensor-schedule joiners use the same
> offsets as the light-show parser that was actually read.


Scope: the ~25 `Query*` classes in
`com/gelighting/cbygekit/services/devices/command/` that `cyncdec opcodes`
reported no opcode for.

All class paths below are **relative to `sources/`**. Every claim is tagged
**confirmed** / **plausible** / **not found**.

---

## 0. Why the extractor missed these (the actual root cause)

**confirmed.** These classes are *not* opcode-less. They declare ordinary static
`byte[]` opcode arrays — but JADX rendered the value `16` inside the array as a
reference to an unrelated vendored constant of the same value:

```java
// com/gelighting/cbygekit/services/devices/command/QueryDeviceTypeAndVersionCommand.java
f34542w = new byte[]{-57, 17, 2, Tnaf.POW_2_WIDTH, 0};
```

`com/thingclips/bouncycastle/math/p055ec/Tnaf.java:10` reads
`public static final byte POW_2_WIDTH = 16;` → `0x10`. So the array is
`C7 11 02 10 00`.

`tools/cyncdec/opcodes.py` matches on all-numeric-literal `byte[]{...}`
initialisers, so a single symbolic element made the whole array invisible. Every
one of the 16 "missing" arrays below fails for exactly this reason and no other.
Fixing the extractor to substitute known vendor `static final byte/int`
constants would recover them all mechanically.

The genuinely array-less commands are a separate group: they are Xlink-only hub
commands that pass a bare op byte to the delegate (§4).

---

## 1. The shared query mechanism

**confirmed.** `com/gelighting/cbygekit/services/devices/command/StatusNotificationQueryCommand.java`

`StatusNotificationQueryCommand<T extends StatusNotification>` is the shared base.
It carries **no opcode of its own** and takes none by constructor. What it takes is
the *response* type:

```java
public StatusNotificationQueryCommand(@NotNull KClass<? extends T> notificationClass)
```

Each subclass calls `super(Reflection.getOrCreateKotlinClass(XxxNotification.class))`.
That `KClass` is stored in `f35027n` and used purely as a receive-side filter.

The opcode instead comes from two **overridable send hooks**, both of which throw
`UnsupportedOperationException` in the base:

| Base method (this decompile) | Kotlin name | Role |
| --- | --- | --- |
| `mo14060M(TelinkCommandDelegate, MeshAddress, Continuation)` | `sendTelinkRequest` | BLE-mesh request |
| `mo14023N(XlinkCommandDelegate, MeshAddress, Continuation)` | `sendXlinkRequest` | Xlink/cloud request |

Consequence, and it matters: **a `Query*` class that overrides only one of these
supports only that transport.** Calling the other path raises
`UnsupportedOperationException`, it does not silently fall back. `QueryWifiStatusCommand`
and `QueryMotionSensorScheduleCommand` are Telink-only; the seven hub commands in §4
are Xlink-only.

The base's own `mo14012f`/`mo14013g` (the standard `DeviceCommand` transport pair)
are `final` here: they subscribe the notification observer first
(`m14128H`/`m14129I`, gated by mutex `f35028o`), *then* call the subclass hook. The
listener-before-send ordering, msgId correlation, and timeout layers are already
documented in the inline `[cync-lan reverse-engineering note ...]` block at the top
of that file and are not re-derived here.

Other overridable hooks used by these commands:

| Method | Meaning | Default |
| --- | --- | --- |
| `getF35006A()` (`D`) | overall query deadline, ms | `10000` |
| `mo14067B(ExecutionType)` | pre-send settle delay | TELINK `0`, XLINK `1000` ms |
| `mo14068F()` (`F`) | `StatusNotification.Type`s to pre-register with the parser | empty |
| `mo14066z(T)` (`z`) | extra per-notification accept filter | `true` |
| `mo14072J(T)` (`J`) | "this response completes the query" for multi-response queries | `false` |

`StatusNotificationQueryCommandKt` is **not** part of the opcode path — it only
wraps a query in `IgnoreResultDeviceCommand` (`m14134a`) or in a 3-attempt retry
flow (`m14135b`). **confirmed.**

---

## 2. The BLE-mesh (Telink) query envelope

### 2.1 Request

**confirmed.** `mo14060M` always calls
`telinkCommandDelegate.mo14046d(body, meshAddress, 0, continuation)`; the impl is
`com/gelighting/cbygekit/services/devices/telink/TelinkDeviceBleManager.java:435`
→ `m14326L` (`writeCommand`, same file, line 1276), which already carries a
validated cync-lan note. `body` lands at `packet[7:]` of the 20-byte encrypted
mesh packet:

| Offset | Field |
| --- | --- |
| `0..1` | seq (random) |
| `2` | the `i` argument — **always `0`** for every query here |
| `3..4` | MIC |
| `5..6` | destination `MeshAddress`, little-endian |
| `7..` | `body` ← the opcode array |

`body` itself:

| Body offset | Packet offset | Field |
| --- | --- | --- |
| `0` | `7` | **opcode** |
| `1` | `8` | `0x11` — vendor id byte 1 |
| `2` | `9` | `0x02` — vendor id byte 2 |
| `3` | `10` | mode / flags — **`0x10` on every `Query*` in this set** |
| `4` | `11` | parameter selector (only in the `0xEA`/`0xE6`/`0xC0`/`0xDD`/`0xC7` families) |
| `5..` | `12..` | request payload, if any |

The `0x11 0x02` at body[1..2] is **confirmed** independently by the receive side:
`com/gelighting/cbygekit/services/devices/telink/TelinkNotificationParser.java`
rejects any notification where `dataBytes[8] != 0x11 || dataBytes[9] != 0x02`
("Can't parse notification - wrong header").

**plausible:** that body[3] `0x10` means "read/query mode". Supporting evidence:
(a) all 16 static-array queries here use `0x10` there; (b) the corresponding
writers use a *different* opcode with the selector moved up one slot — e.g.
`SetLatLngCommand` sends `F7 11 02 45` where `QueryLatLngCommand` sends
`EA 11 02 10 45`. Counter-evidence you must not ignore: `QueryRgbCommand` (not in
this task's list) sends `EA 11 02 08 22` — same `0xEA` opcode, body[3] = `0x08`.
So body[3] is a mode byte within the family, and `0x10` is the mode these commands
happen to use; it is **not** a structural "this is a read" bit. Confirming this
would need either firmware or a capture of a non-`0x10` read.

### 2.2 Response

**confirmed.** `TelinkNotificationParser.m14349b` dispatches on `dataBytes[7]`
through `com/gelighting/cbygekit/services/devices/telink/NotificationType.java`:

| `dataBytes[7]` | `NotificationType` | Parser |
| --- | --- | --- |
| `0xE1` | ADDRESS | `MeshAddressNotification.TelinkParser` |
| `0xC8` | DEVICE_TYPE_AND_VERSION | `DeviceTypeAndVersionNotification.TelinkParser` |
| `0xD4` | GROUP | `DeviceGroupNotification.TelinkParser` |
| `0xC1` | SCENE | `DeviceSceneNotification.TelinkParser` |
| `0xE7` | AUTOMATION | `AutomationNotification.TelinkParser` |
| `0xDC` | MESH_STATUS | `MeshStatusNotification.TelinkParser` |
| `0xF6` | WIFI | `TelinkWifiNotificationParser` |
| `0xE9` | QUERY_TIME | `DeviceTimeNotification.TelinkParser` |
| `0xEA` | BASE_EA | `TelinkBaseNotificationParser` (sub-dispatch, below) |
| `0xEB` | RGB_STATUS | `RgbStatusNotification.TelinkParser` |

Notification frame layout (**confirmed**, from
`TelinkNotificationParser.m14349b`,
`model/notification/TelinkStatusNotificationParser.java` and every leaf parser):

| Offset | Field |
| --- | --- |
| `3..4` | **source** `MeshAddress`, u16 LE (`TelinkStatusNotificationParser.m14309c` → `DataBytes.m15001d(3)`) |
| `7` | response opcode (table above) |
| `8` | `0x11` |
| `9` | `0x02` |
| `10` | mode / flags (varies; some parsers read payload from here) |
| `11` | **subtype**, for the `0xEA` family |
| `12..` | payload |

`0xEA` sub-dispatch — `com/gelighting/cbygekit/services/devices/telink/TelinkBaseNotificationParser.java`,
`Subtype` enum keyed on `dataBytes[11]`. Entries relevant to this task:

| `dataBytes[11]` | Subtype | Notification |
| --- | --- | --- |
| `0x00` | MOTION_SENSOR_SCHEDULE | `TelinkMotionSensorScheduleNotificationParser` (matched by *expected type*, not byte code — its ctor flag `mustBeExpected=true` forbids a byte code) |
| `0x81` | BASE_DEVICE_SETTINGS | `BaseDeviceSettingsNotification` |
| `0x82` | MOTION_SENSOR_SETTINGS | `MotionSensorSettingsNotification` |
| `0x85` | FIRMWARE_VERSION | `FirmwareVersionNotification` |
| `0x86` | OTA_UPDATE_STATUS | `OtaUpdateStatusNotification` |
| `0x87` | WIFI_CONNECTION_STATUS | `WifiConnectionStatusNotification` |
| `0x89` | QUERY_WIFI_RSSI_AND_IP | `WifiRssiAndIpNotification` |
| `0xC0` | LIGHT_MUSIC_SHOW_SETTINGS | `LightMusicShowSettingsNotification` |
| `0xC5` | LAT_LNG | `LatLngNotification` |
| `0xC6` | QUERY_LIGHT_SHOW_SETTINGS_MULTIPART | `TelinkLightShowSettingsNotificationParser` |
| `0xC7` | QUERY_MUSIC_SHOW_SETTINGS_MULTIPART | `TelinkMusicShowSettingsNotificationParser` |
| `0xD0` | QUERY_MULTI_COLOR_SETTINGS | `MultiColorSettingsNotification` |
| `0xEE` | QUERY_SUNRISE_SUNSET_TIME | `CelestialTimeNotification` |
| `0xF4` | QUERY_HARDWARE_VERSION | `HardwareVersionNotification` |

Independent cross-check for the `[7]`/`[11]` positions: `BaseDeviceSettingsNotification.java`
re-tests `data[7] == 0xEA && data[11] == 0x81` inside its own parser before reading
its payload. **confirmed.**

Also in that class: `f36808b = {0x05, 0x10}` — subtypes at `dataBytes[11]` that are
explicitly thrown as `UnsupportedNotificationException(..., true)`. **not found:**
what these two actually are. `0x05`/`0x10` are exactly the request-side selector
and the request-side mode byte, so an echoed-request filter is a natural reading,
but nothing in the decompile states it.

### 2.3 The `| 0x80` selector rule

Pairing every request selector with the subtype its response arrives under:

| Request `body[4]` | Response `dataBytes[11]` |
| --- | --- |
| `0x01` | `0x81` |
| `0x02` | `0x82` |
| `0x05` | `0x85` |
| `0x06` | `0x86` |
| `0x07` | `0x87` |
| `0x09` | `0x89` |
| `0x40` | `0xC0` |
| `0x45` | `0xC5` |
| `0x46` | `0xC6` |
| `0x47` | `0xC7` |
| `0xEE` | `0xEE` |
| `0xF4` | `0xF4` |

Each individual row is **confirmed** (request array in the command class, subtype
in `TelinkBaseNotificationParser$Subtype`). The generalisation
**`response_subtype = request_selector | 0x80`** is **plausible** — it holds for all
12 observed pairs with no exceptions, but nothing in the decompile computes it;
the mapping is a hand-written enum. Do not extrapolate it to a selector you have
not seen in this table. Note it does *not* relate the `0xF7` write selectors to
anything (`SetMotionSensorSettingsCommand` writes `F7 11 02 07` while
`QueryMotionSensorSettingsCommand` reads selector `0x02`) — writes have their own
selector space.

Top-level opcodes follow a looser request→response `+1` pattern
(`0xC7→0xC8`, `0xC0→0xC1`, `0xE6→0xE7`, `0xE8→0xE9`, `0xEA→0xEA`), **broken** by
groups: `0xDD→0xD4`. **confirmed** per pair, **do not generalise.**

---

## 3. The Xlink envelope for mesh-relayed queries

**confirmed.** Every `mo14023N` in §5's "both" rows calls
`XlinkCommandDelegate.DefaultImpls.m14394c(delegate, body, meshAddress, 0, cont, 12)`
(`services/devices/xlink/XlinkCommandDelegate.java`), which forwards to
`mo14056h`, which is
`services/devices/xlink/XlinkDeviceManager.java:1045`:

```java
public final Object mo14056h(byte[] bArr, MeshAddress meshAddress, int i, int i2, Continuation c) {
    return mo14054f((byte) -114, bArr, meshAddress, i, i2, c);   // 0x8E
}
```

**The outer Xlink op_code for all of these is `0x8E`, not the first byte of the
opcode array.** The array is payload. This is the "0x8E mesh-relay" family already
documented on `XlinkDeviceManager.java` and confirmed there against a real capture.

`mo14054f` (line 984) then builds:

| Offset | Field |
| --- | --- |
| `0..2` | msgId, 3 bytes little-endian (`i2 & 0xFF`, `>>8`, `>>16`) |
| `3..4` | `0x00 0x00` (`writeShort(0)`) |
| `5..6` | destination `MeshAddress`, u16 LE (`ExtensionsKt.m13359f`) |
| `7..` | the opcode array, verbatim |

…and hands it to `mo14055g` → `Xlink.m14391a(op, data, msgId)`
(`services/devices/xlink/Xlink.java`), the HDLC/PPP frame:

```
0x7E [msgId 4B LE] 0xF8 [op_code 1B] [len 2B LE] [data] [checksum 1B] 0x7E
```

with `0x7D`/`0x7E` byte-stuffing (`0x7E`→`7D 5E`, `0x7D`→`7D 5D`) and checksum =
`sum(op || len || data) mod 256`. Whether this legacy `@Deprecated` framing is what
rides cync-lan's TCP relay is still the open question flagged on `Xlink.java` —
**plausible, not confirmed**, unchanged by this pass.

**Response side, confirmed:**
`services/devices/xlink/XlinkNotificationParser.java` decodes the inbound HDLC
frame (`bArr3[5]` = direction `0xF8` REQ / `0xF9` RSP / `0xFA` ANNOUNCE,
`bArr3[6]` = op_code, `bArr3[7:8]` = length BE, `bArr3[9 .. len-3]` = payload) and
dispatches op_code through an `EnumMap<XlinkCommandCode, StatusNotificationParser>`
(line 83 ff.). For `PASSTHROUGH_8E` it installs
`XlinkPassthroughNotificationParser(new TelinkNotificationParser(...))`, and that
parser passes the frame payload through **unmodified** to
`TelinkNotificationParser.m14349b`
(`model/notification/XlinkPassthroughNotificationParser.java:113`).

So a `0x8E` response payload is indexed with the *Telink* offsets of §2.2:
`[7]`=opcode, `[8]=0x11`, `[9]=0x02`, `[11]`=subtype, `[12..]`=payload — i.e. the
7-byte routing prefix the outgoing side prepends is mirrored on the way back. **The
byte layout of a query response is identical over both transports**; only the outer
wrapper differs.

---

## 4. The Xlink-only hub queries

**confirmed.** Seven commands override only `mo14023N` and send a *real* op byte,
either via `DefaultImpls.m14393b(delegate, op, payload, cont)` → `mo14055g` →
`Xlink.m14391a`, or via `XlinkTranslatorKt.m14449a(msgId, op, buf)` + `mo14053e`.
Those two routes are byte-identical — `m14449a` is a one-line wrapper around
`Xlink.m14391a` (`services/devices/xlink/legacy/XlinkTranslatorKt.java`). There is
**no 7-byte routing prefix** on this route; the payload is written raw.

Op-byte names come from `services/devices/xlink/XlinkCommandCode.java`. Caveat
already noted in that file: JADX failed to restore the pseudo-enum and reused
labels for some entries, so **go by byte value**. Each byte below is independently
corroborated by the command class that emits it.

| Op | `XlinkCommandCode` label | Emitted by |
| --- | --- | --- |
| `0x46` | HUB_TIME_QUERY | `QueryDeviceTimeCommand` (hub/WiFi branch) |
| `0x49` | HUB_CHECK_UPDATE | `QueryHubFirmwareUpdatesCommand` (frame 1) |
| `0x4B` | QUERY_HUB_INFO | `QueryHubInfoCommand` |
| `0x51` | QUERY_HUB_DEVICE_INFO_PAGES | `QueryHubDeviceListCommand` |
| `0x52` | QUERY_DEVICE_STATUS_PAGES | `QueryMeshStatusCommand` |
| `0x88` | MULTI_COLOR_SETTINGS | `QueryMultiColorSettingsDirectCommand` |
| `0x8A` | QUERY_HUB_MESH_NAME_AND_PASSWORD | `QueryHubMeshNameAndPasswordCommand` |
| `0x8C` | HUB_PASSTHROUGH_8C | `QueryHubFirmwareUpdatesCommand` (frame 2) |
| `0xAD` | GET_SOL_LED_CONFIG | `QuerySolConfigCommand` |

**Responses come back under the same op byte** — `XlinkNotificationParser`'s
`EnumMap` registers `QUERY_HUB_INFO → HubInfoNotification.XlinkParser`,
`QUERY_HUB_DEVICE_INFO_PAGES → HubDeviceListNotification.XlinkParser`,
`QUERY_DEVICE_STATUS_PAGES → MeshStatusNotification.XlinkParsers.DeviceStatusPagesParser`,
`QUERY_HUB_MESH_NAME_AND_PASSWORD → HubMeshNameAndPasswordNotification.XlinkParser`,
`HUB_TIME_QUERY → DeviceTimeNotification.XlinkParser`,
`GET_SOL_LED_CONFIG → SolConfigNotification.XlinkParser`,
`MULTI_COLOR_SETTINGS → MultiColorSettingsDirectNotification.XlinkParser`,
`HUB_CHECK_UPDATE → HubFirmwareUpdatesNotification.XlinkParser`. **confirmed.**

`0x8C` (`HUB_PASSTHROUGH_8C`) is **absent from the map** — nothing parses a `0x8C`
response. **confirmed absence**, meaning `QueryHubFirmwareUpdatesCommand`'s second
frame is fire-and-forget and the answer it awaits arrives on `0x49`.

Payload integers on this route are written with `ExtensionsKt.m13359f`
(`foundation/ExtensionsKt.java:81`) = **u16 little-endian**. **confirmed.**

---

## 5. Per-command table

`Body` = the bytes handed to the transport delegate.
`T` = Telink/BLE path present, `X` = Xlink path present. A missing letter means
that transport throws `UnsupportedOperationException`.

| Command class | T | X | Request bytes | Response |
| --- | :-: | :-: | --- | --- |
| `QueryAutomationCommand` | ✓ | 0x8E | `E6 11 02 10 <scheduleId or FF>` | `AutomationNotification`, telink op `0xE7`; payload `[10..19]` |
| `QueryBaseDeviceSettingsCommand` | ✓ | 0x8E | `EA 11 02 10 01` | `BaseDeviceSettingsNotification`, `0xEA`/sub `0x81`; flags at `[12]` (bits 7,6,3 = load type; bit 5 = enable), LED indicator at `[18]` (hi nibble mode, lo nibble colour), brightness `[19]` |
| `QueryCelestialTimeCommand` | ✓ | 0x8E | `EA 11 02 10 EE` | `CelestialTimeNotification`, `0xEA`/`0xEE`; `[12]` must be `1`, sunrise `[13]`h `[14]`m, sunset `[16]`h `[17]`m, UTC-offset hours `[19]` (signed) |
| `QueryDeviceGroupCommand` | ✓ | 0x8E | `DD 11 02 10 01` | `DeviceGroupNotification`, telink op `0xD4`; payload `[10..17]` |
| `QueryDeviceSceneCommand` | ✓ | 0x8E | `C0 11 02 10 FF` | `DeviceSceneNotification`, telink op `0xC1`; payload `[10..19]` |
| `QueryDeviceTimeCommand` | ✓ | **split** | Telink `E8 11 02 10`. Xlink: if `deviceType.productType.f31219d` → HDLC op **`0x46`**, empty payload; else `0x8E` relay of `E8 11 02 10` | `DeviceTimeNotification`. Telink op `0xE9`, u16 LE year at `[10]`, `[18]`, `[19]`. Xlink op `0x46`, `XlinkParser`, u16 LE at `[0]` |
| `QueryDeviceTypeAndVersionCommand` | ✓ | 0x8E | `C7 11 02 10 00` | `DeviceTypeAndVersionNotification`, telink op `0xC8`; `[11..14]` + u16 LE `[15]` |
| `QueryFirmwareVersionCommand` | ✓ | 0x8E | `EA 11 02 10 05` | `FirmwareVersionNotification`, `0xEA`/`0x85`; version digits `[12..16]` |
| `QueryHardwareVersionCommand` | ✓ | 0x8E | `EA 11 02 10 F4` | `HardwareVersionNotification`, `0xEA`/`0xF4`; single byte `[12]` |
| `QueryHubDeviceListCommand` | — | **`0x51`** | u16LE `0x0000` ‖ u16LE `total` ‖ u16LE `offset` (`total` defaults `-1`) | `HubDeviceListNotification`, op `0x51`; u16 LE at payload `[4]` |
| `QueryHubFirmwareUpdatesCommand` | — | **`0x49` then `0x8C`** | frame 1 op `0x49`, empty. frame 2 op `0x8C`, payload `00 00 01 00 00 01 00 EA 11 02 10 06` | `HubFirmwareUpdatesNotification` on op `0x49`. Nothing parses `0x8C` |
| `QueryHubInfoCommand` | — | **`0x4B`** | empty | `HubInfoNotification`, op `0x4B`; four consecutive 16-byte strings |
| `QueryHubMeshNameAndPasswordCommand` | — | **`0x8A`** | empty | `HubMeshNameAndPasswordNotification`, op `0x8A`; two NUL-trimmed strings |
| `QueryLatLngCommand` | ✓ | 0x8E | `EA 11 02 10 45` | `LatLngNotification`, `0xEA`/`0xC5`; lat = s16 LE `[12..13]` + u16 LE `[14..15]`/1e4 signed by the integer part; lng = same at `[16..17]` / `[18..19]` |
| `QueryLightMusicShowSettingsCommand` | ✓ | 0x8E | `EA 11 02 10 40` | `LightMusicShowSettingsNotification`, `0xEA`/`0xC0`; `[12]` run-mode code (0 Static, 1 LightShow, 2 MusicShow, 3 Reveal, 4 MultiColor), `[13]` show index, `[14]` |
| `QueryLightShowSettingsCommand` | ✓ | 0x8E | `EA 11 02 10 46 <showIndex>` | `LightShowSettingsNotification`, `0xEA`/`0xC6`, **multipart** via `TelinkLightShowSettingsNotificationParser` + `…MultipartNotificationJoiner`. Command re-filters on `notification.f36510e == showIndex` |
| `QueryMeshStatusCommand` | — | **`0x52`** | u16LE `0x0000` ‖ u16LE `total` ‖ u16LE `offset` (`total` defaults `-1`) | `MeshStatusNotification.XlinkParsers.DeviceStatusPagesParser`, op `0x52` |
| `QueryMotionSensorScheduleCommand` | ✓ | — | `EA 11 02 10 04` ‖ u16LE `groupId` ‖ `FF` (built through `ByteArrayOutputStream`) | `MotionSensorScheduleNotification`, `0xEA`, subtype `0x00` — matched by pre-registered `StatusNotification.Type.MOTION_SENSOR_SCHEDULE` (`mo14068F()`), not by a byte code. Multipart |
| `QueryMotionSensorSettingsCommand` | ✓ | 0x8E | `EA 11 02 10 02` | `MotionSensorSettingsNotification`, `0xEA`/`0x82`; `[12]`, `[13]`, u16 **BE** `[14]`, u16 **BE** `[16]`, `[18]`, `[19]` |
| `QueryMultiColorSettingsDirectCommand` | — | **`0x88`** | two u16LE values from the `Query` subclass. **caveat:** both render as literal `0` in this decompile for both `AllSegments` and `Segments` — JADX constant-folded the field reads away | `MultiColorSettingsDirectNotification`, op `0x88`; u16LE `[0]`, flag `[2]`, u16LE `[3]`, u16LE `[5]`, count `[7]`, then per-segment `[8+i]` |
| `QueryMusicShowSettingsCommand` | ✓ | 0x8E | `EA 11 02 10 47 <showIndex>` | `MusicShowSettingsNotification`, `0xEA`/`0xC7`, **multipart**. Command re-filters on `notification.f36593e == showIndex` |
| `QuerySolConfigCommand` | — | **`0xAD`** | empty | `SolConfigNotification`, op `0xAD` |
| `QueryWifiOtaUpdateStatusCommand` | ✓ | 0x8E | `EA 11 02 10 06` | `OtaUpdateStatusNotification`, `0xEA`/`0x86`; state `[12]`, progress/percent `[13]`, version `[14..18]`. Multi-response: completes only when `mo14072J` sees AutoUpdate / Checking-NoUpdate / 100% / Failed |
| `QueryWifiRssiAndIpCommand` | ✓ | 0x8E | `EA 11 02 10 09` | `WifiRssiAndIpNotification`, `0xEA`/`0x89`; `[12]` = signed RSSI (`0` ⇒ not connected), `[13..16]` = 4 IPv4 octets |
| `QueryWifiStatusCommand` | ✓ | — | `EA 11 02 10 07` | `WifiConnectionStatusNotification`, `0xEA`/`0x87`; bytes `[13..18]` (exclusive upper) |

Cross-reference — two adjacent classes *not* in the task list that the extractor
also mishandled and that constrain the readings above:

- `QueryRgbCommand` — `EA 11 02 08 22`, response op `0xEB` RGB_STATUS. Shows body[3] is not fixed at `0x10`.
- `QueryMeshAddressCommand` — `E0 11 02 FF FF`, response op `0xE1` ADDRESS. Shows some queries carry no `0x10`/selector at all.
- `QueryMultiColorSettingsCommand` — `F7 11 02 50` + params, response `0xEA`/`0xD0`. Shows `0xF7` is not exclusively a write opcode.

---

## 6. Open questions / not found

1. **Xlink outer-op equality.** For the mesh-relayed set, the outer Xlink op is
   `0x8E` for *all* of them and the "opcode" differs only in the relayed payload.
   That is a real transport asymmetry versus Telink, where the array's first byte
   *is* the wire opcode. **confirmed** — but see the `SetFanSpeedCommand` precedent
   (`F4 11 02 01` BLE vs `E2 11 02 06` Xlink): no `Query*` here shows an equivalent
   per-transport payload divergence, and I found none.
2. **`0x8C` request payload semantics.** `QueryHubFirmwareUpdatesCommand`'s literal
   `00 00 01 00 00 01 00 EA 11 02 10 06` has the exact shape of `mo14054f`'s output
   (msgId 3B LE = `0x010000`, `0x0000`, dest u16 LE = `0x0001`, then a `0xEA` mesh
   body) but is hard-coded and sent through the raw path. **plausible** that `0x8C`
   is a hub-side "relay this mesh body to address 1" op. Confirming it needs either
   a live capture or the hub firmware.
3. **`TelinkBaseNotificationParser.f36808b = {0x05, 0x10}`.** **not found** what
   these two rejected subtypes are.
4. **`ProductType.f31219d`.** The flag `QueryDeviceTimeCommand` branches on. Its
   Kotlin name is lost. Everywhere else in the tree it selects
   `ConnectionType.WIFI` and identifies the hub device
   (`services/devices/DeviceManagerImpl.java:989`,
   `DeviceServiceDefault$findHubDeviceManager$2$hubDevice$1.java`), so "is a
   WiFi/hub product" is **plausible**, not proven.
5. **Multipart reassembly.** The three multipart families
   (`TelinkLightShowSettings…`, `TelinkMusicShowSettings…`, `TelinkMotionSensorSchedule…`,
   plus `TelinkWifi…`) each have a `MultipartStartNotification` /
   `MultipartNotification` / `…Joiner` triple in
   `services/devices/telink/`. Their chunk-header layout was **not traced** in this
   pass — if cync-lan needs to reassemble light-show or motion-sensor-schedule
   responses, that is the next thing to read.
6. **`QueryMultiColorSettingsDirectCommand` request payload.** Both `Query` variants
   emit `0x0000 0x0000` in this decompile. **not found** whether the real values are
   `firstPhysicalSegmentNumber`/`segmentCount` fields JADX folded away, or genuinely
   constant zeros. The `toString()` renders
   `Segments(firstPhysicalSegmentNumber=0, segmentCount=0)` with literals too, which
   points at a JADX artefact rather than real constants — but that is inference.

---

## 7. Suggested extractor fix

`tools/cyncdec/opcodes.py` currently drops any `byte[]{...}` containing a
non-numeric element. Resolving `static final byte`/`int` constants from the class
index (there are only a handful in practice — `Tnaf.POW_2_WIDTH` accounts for all
16 misses here) would move these 16 commands from "could not be tied to a send
call" into the normal telink/xlink columns automatically. The remaining nine
(§4 plus `QueryMotionSensorScheduleCommand`) legitimately need method-body reading,
since they pass a bare op byte or assemble through a `ByteArrayOutputStream`.
