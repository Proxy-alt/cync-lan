# Multipart / inner-class-send commands

Eleven command classes whose static opcode array `cyncdec opcodes` could not tie to a send
call, because the send happens inside a JADX-generated `$sendTelinkRequest$N` /
`$sendXlinkRequest$N` inner class or inside a shared chunking helper.

All paths below are **relative to `sources/`**. Method names (`mo14012f`, `m14027t`, …) are
R8-renamed and renumber on re-decompile — they are cited as locators, not identifiers.

Evidence tiers used throughout: **confirmed** (directly readable in the cited class),
**plausible** (reasonable inference, states what would confirm it), **not found**.

---

## Summary table

| Class | Telink (BLE mesh) opcode | Xlink inner opcode | Xlink outer op | Chunked? | `blockSize` | WriteType | ConnectionType |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `SetLightShowCommand` | `F7 11 02 43` | `F7 11 02 43` (same) | `0x8E` | yes | 9 | DEFAULT | BLE, BLE_PROXY, WIFI, WIFI_PROXY |
| `SetLightShowExtendedCommand` | `F7 11 02 57` | `F7 11 02 57` (same) | `0x8E` | yes | 9 | DEFAULT | BLE, BLE_PROXY, WIFI, WIFI_PROXY |
| `SetMusicShowCommand` | `F7 11 02 44` | `F7 11 02 44` (same) | `0x8E` | yes | 9 | DEFAULT | BLE, BLE_PROXY, WIFI, WIFI_PROXY |
| `SetMusicShowExtendedCommand` | `F7 11 02 58` | `F7 11 02 58` (same) | `0x8E` | yes | 9 | DEFAULT | BLE, BLE_PROXY, WIFI, WIFI_PROXY |
| `SetMultiColorBitmapCommand` | `F7 11 02 4F` | `F7 11 02 4F` (same) | `0x8E` | yes | 9 | DEFAULT | BLE, BLE_PROXY, WIFI, WIFI_PROXY |
| `SaveMultiColorBitmapCommand` | `F7 11 02 56` **+ action byte** | same | `0x8E` | **only for `SendBitmap`** | 8 | DEFAULT | BLE, BLE_PROXY, WIFI, WIFI_PROXY |
| `SetTileLayoutCommand` | `F7 11 02 53` | `F7 11 02 53` (same) | `0x8E` | yes | 9 | DEFAULT | BLE, BLE_PROXY, WIFI, WIFI_PROXY |
| `SetCustomButtonOptionCommand` | `F7 11 02 2C` | `F7 11 02 2C` (same) | `0x8E` | yes | 9 | DEFAULT | BLE, BLE_PROXY, WIFI, WIFI_PROXY |
| `SetSameGroupDeviceIdsCommand` | `F7 11 02 61` | `F7 11 02 61` (same) | `0x8E` | yes | 9 | DEFAULT | BLE, BLE_PROXY, WIFI, WIFI_PROXY |
| `SetMeshAddressCommand` | `E0 11 02` | **no Xlink path** | — | **no** | — | DEFAULT | BLE only |
| `SetWifiCommand` | `F6 11 02 02` | **no Xlink path** | — | yes, **own scheme** | 8 (fixed) | DEFAULT | BLE only |

**"same" means byte-identical opcode array.** The *payload body* is byte-identical between
transports too for every command here **except** the four show commands, where one flag byte
depends on the delegate (see [The `noResult` flag](#the-noresult-flag)).

No class in this set overrides `getF34733s()` (`WriteType`), so all inherit
`DeviceCommand.WriteType.f34481a = DEFAULT` — confirmed:
`grep -c WriteType` returns 0 in all eleven files, and the base default is at
`com/gelighting/cbygekit/services/devices/command/DeviceCommand.java`.

---

## 1. The chunking scheme (`sendBlocks`)

**Confirmed** — `com/gelighting/cbygekit/services/devices/command/DeviceCommand.java`, static
method `m14027t`. JADX failed to structure it (`JadxOverflowException`) and emitted raw
smali-like bytecode, which is why the extractor could not follow it; the bytecode is
unambiguous. The `@DebugMetadata` on the continuation class names it
`DeviceCommand$Companion$sendBlocks$1`, i.e. the Kotlin source name is **`sendBlocks`**.

Signature:

```
sendBlocks(
    opcode:      byte[],     // the class's static OPCODE_BYTES
    payload:     byte[],     // command-specific body
    blockSize:   int,        // the Companion's BLOCK_SIZE constant
    delayMillis: long,       // inter-block delay; 0 for every command in this set
    send:        (blockIndex: Int, commandBody: ByteArray) -> Unit,
    continuation
)
```

Behaviour:

```
stream  = [0x00] ++ payload                  // 0x00 is a placeholder
chunks  = stream.chunked(blockSize - 1)      // ExtensionsKt.m13354a(size, bytes)
chunks[0][0] = (byte) chunks.size()          // placeholder := TOTAL BLOCK COUNT

for (i, chunk) in chunks.withIndex():
    if (i != 0) delay(delayMillis)
    send(i, opcode ++ [(byte)(i + 1)] ++ chunk)
```

So the **command body** on the wire is:

| Block | Bytes |
| --- | --- |
| block 1 (`i = 0`) | `OPCODE …` ‖ `0x01` ‖ `totalBlockCount(1B)` ‖ `payload[0 .. blockSize-3]` |
| block *n* (`i = n-1`) | `OPCODE …` ‖ `n` ‖ next `blockSize-1` payload bytes |

- Block number in the body is **1-based**.
- The total-block-count byte appears **only in block 1**, immediately after the block number.
- Block 1 therefore carries `blockSize - 2` payload bytes; every later block carries
  `blockSize - 1`.
- **There is no terminator.** The receiver knows it is done when it has collected
  `totalBlockCount` blocks — confirmed on the receive side by
  `com/gelighting/cbygekit/services/devices/model/notification/AbstractMultipartNotificationJoiner.java`
  (`isComplete()` = `parts.size() == partCount`).
- `ExtensionsKt.m13354a(int, byte[])` is `CollectionsKt.chunked(...)` — confirmed at
  `com/gelighting/cbygekit/foundation/ExtensionsKt.java:22`.

### Why `blockSize` is 9 (and 8)

**Confirmed** — `com/gelighting/cbygekit/services/devices/telink/TelinkDeviceBleManager.java`,
`m14326L` ("writeCommand"). The 20-byte Telink packet is built as:

```
packet[0:2]  random seq (2B)
packet[2]    the `i` argument to mo14046d  ← sendBlocks passes the 0-BASED blockIndex here
packet[3:5]  MIC
packet[5:7]  destination MeshAddress, little-endian
packet[7:20] commandBody
```

Source line: `new byte[]{(byte) i, 0, 0, bArrCopyOf3[1], bArrCopyOf3[0]}` prefixed by 2 random
bytes, then `ArraysKt.plus(..., commandBody)`.

That leaves exactly 13 bytes for the command body, and every choice of `blockSize` in this set
fills it precisely:

| Command | opcode len | + block no. | + chunk | = body |
| --- | ---: | ---: | ---: | ---: |
| `blockSize = 9`, 4-byte opcode | 4 | 1 | 8 | **13** |
| `SaveMultiColorBitmapCommand` (`blockSize = 8`, 4-byte opcode + 1 action byte) | 5 | 1 | 7 | **13** |
| `SetWifiCommand` (own chunker, 4-byte opcode) | 4 | 1 | 8 | **13** |

This is a strong structural confirmation of the reading: the chunk size is chosen so each
mesh packet is exactly full.

### Receive-side mirror (independent cross-validation)

**Confirmed** —
`com/gelighting/cbygekit/services/devices/telink/TelinkLightShowSettingsNotificationParser.java`:

```
sequence = dataBytes[12]
if (sequence != 1)  -> TelinkLightShowSettingsMultipartNotification(sequence, dataBytes[13..])
else                -> partCount = dataBytes[13]
                       TelinkLightShowSettingsMultipartStartNotification(partCount, dataBytes[14..])
```

Same shape, opposite direction: a 1-based sequence byte, and the *first* packet carries an
extra total-count byte immediately after it. This was derived independently of `sendBlocks`
and agrees with it exactly.

`AbstractMultipartNotificationJoiner.mo14289a()` then sorts parts, asserts
`part.sequence == index + 1` (throws `DeviceNotificationException("Wrong sequence")`
otherwise), concatenates payloads, and hands the joined buffer to `mo14291c(byte[])`.

> **Caveat (plausible, not confirmed):** the notification's *joined* buffer for light-show
> settings (parsed in `TelinkLightShowSettingsMultipartNotificationJoiner.java`) is a
> **report** layout, not the `SetLightShow*` request layout — it has a 2-byte-LE `×100`
> encoding for speeds and an effect byte at `[1]`, i.e. it merges what the base and extended
> *set* commands send separately. Do not reuse the request layouts below to decode it.

---

## 2. The Xlink path

**Confirmed** — all nine Xlink-capable commands here route
`sendBlocks`'s `send` lambda through
`XlinkCommandDelegate.DefaultImpls.m14394c(delegate, body, meshAddress, blockIndex, cont, 4)`
(`com/gelighting/cbygekit/services/devices/xlink/XlinkCommandDelegate.java`), verified by
grepping every `*$sendXlinkRequest$2.java` in the command package — all nine match that call
verbatim, differing only in field numbers.

`DefaultImpls.m14394c` → `mo14056h(body, meshAddress, msgId, blockIndex)` → hardcodes the
outer op and calls `mo14054f((byte) 0x8E, …)`.

So **every command in this set that has an Xlink path is a member of the documented
"0x8E mesh-relay bug" family**: the whole `F7 11 02 xx …` array travels as opaque payload
under outer opcode `0x8E`, exactly as already noted on `ExecuteSceneCommand`,
`SetStatusIndicatorSettingsCommand`, and the motion-sensor commands. Confirmed at
`com/gelighting/cbygekit/services/devices/xlink/XlinkDeviceManager.java:1045`
(`mo14054f((byte) -114, …)`, `-114 == 0x8E`).

### The 3-byte header field carries the block index, not the message id

**Confirmed, and this contradicts the existing inline note's general description** —
`XlinkDeviceManager.CommandDelegate.mo14054f`:

```java
mo14054f(byte b, byte[] commandBody, MeshAddress destination, int i, int i2, …) {
    writeByte(i2 & 255);            // 3-byte little-endian field
    writeByte((i2 >> 8) & 255);
    writeByte((i2 >> 16) & 255);
    writeShort(0);                  // 2 zero bytes
    ExtensionsKt.m13359f(out, destination.f31064a & 0xFFFF);   // 2-byte LE address
    write(commandBody);
    …
    mo14055g(b, frame, i);          // `i` (the msgId) goes to the frame layer
}
```

The 3-byte LE field is `i2`, **not** `i`. Tracing the two argument paths:

| Call site | `i` | `i2` | 3-byte field holds |
| --- | --- | --- | --- |
| `DefaultImpls.m14392a` (ordinary single-shot commands, e.g. `SetBrightnessCommand`) | msgId | msgId | **msgId** |
| `DefaultImpls.m14394c(..., mask = 4)` — **all nine chunked commands here** | msgId | blockIndex | **blockIndex (0-based)** |
| `DefaultImpls.m14394c(..., mask = 12)` — e.g. `ExecuteSceneCommand` | msgId | msgId | msgId |

(`m14394c` computes `(mask & 8) != 0 ? msgId : i`; mask `4` ⇒ blockIndex, mask `12` ⇒ msgId.)

This exactly parallels the Telink side, where `packet[2]` holds the same 0-based block index.
The existing note on `XlinkDeviceManager.java` describes the field as "msgId (3B LE)", which is
correct for the ordinary path but **wrong for chunked sends** — worth amending in that note.

The frame-level msgId used for response correlation (`StatusNotificationQueryCommand`'s msgId
echo check) is unaffected: it is the separate `i` parameter passed to `mo14055g` → `Xlink.m14391a`.

---

## 3. The `noResult` flag

**Confirmed.** The four show commands call
`m14090Q/m14093Q/m14105Q/m14108Q(delegate.b(), sendLambda, cont)` where `b()` is
`TelinkCommandDelegate.mo14044b()` / `XlinkCommandDelegate.mo14050b()`.

| Implementation | returns |
| --- | --- |
| `TelinkDeviceBleManager$CommandDelegate.mo14044b()` (`…/telink/TelinkDeviceBleManager.java:344`) | `false` |
| `XlinkDeviceManager$CommandDelegate.mo14050b()` (`…/xlink/XlinkDeviceManager.java:812`) | `false` |
| `IgnoreResultTelinkCommandDelegate.mo14044b()` (`…/command/IgnoreResultTelinkCommandDelegate.java:40`) | `true` |
| `IgnoreResultXlinkCommandDelegate.mo14050b()` (`…/command/IgnoreResultXlinkCommandDelegate.java:40`) | `true` |

Those are the only implementations in the tree (grep over `com/gelighting`). The
`IgnoreResult*` wrappers are installed by `IgnoreResultDeviceCommand`
(`…/command/IgnoreResultDeviceCommand.java:43,51`), which **is** used for show commands:
`com/gelighting/cbygekit/services/show/ShowServiceDefault.java:4081` and eight sibling sites in
`broadcastShowToDevices` wrap the show command before dispatch. So the flag is reachable as
both 0 and 1 on both transports.

Encoding differs between the base and extended variants:

- **Base** (`SetLightShowCommandKt.m14091a`, `SetMusicShowCommandKt.m14106a`):
  OR-ed into bit 7 of the **effect byte** — `if (z2) i |= 128`.
- **Extended** (`SetLightShowExtendedCommand.m14093Q`, `SetMusicShowExtendedCommand.m14108Q`):
  its **own byte**, `writeByte(z2 ? 1 : 0)`, immediately after the show index.

**Plausible** (not directly proven): the flag means "do not send a result notification back".
It is set exactly when the caller has opted out of awaiting the result, and the receive-side
parser (`TelinkLightShowSettingsMultipartNotificationJoiner.mo14291c`) reads `data[1]` as a raw
effect code **without masking bit 7**, so a response never carries it. Confirming this needs a
live capture comparing a wrapped vs. unwrapped send.

---

## 4. Per-command payload layouts

Field names come from `toString()` bodies and validation messages (which survived R8);
numeric encodings are read from the writers. Two shared helpers, both confirmed in
`com/gelighting/cbygekit/foundation/ExtensionsKt.java`:

- `m13357d(out, Double?)` — **1 byte**, `round(v * 10)`, must fit signed byte; writes `0x00` if null.
- `m13358e(out, Double)` — **2 bytes little-endian**, `round(v * 100)`, must fit signed short
  (via `m13359f`, which writes low byte then high byte).

### 4.1 `SetLightShowCommand` — `F7 11 02 43`

`services/devices/command/SetLightShowCommand.java` (`m14090Q`) +
`services/devices/command/SetLightShowCommandKt.java` (`m14091a`, `m14092b`).
`DeviceCommand.m14027t(f34784y, body, 9, 0L, …)` — **blockSize 9**, no delay.

Payload (before chunking):

| Off | Size | Field |
| ---: | ---: | --- |
| 0 | 1 | `showIndex` (range-checked against `LightRunMode.LightShow`) |
| 1 | 1 | `effect` code, `| 0x80` if the noResult flag is set — **or a single `0x00` and nothing else if `lightShow == null`** |
| 2 | 1 | `fadeSpeed × 10`, coerced to 0.1 … 10.0 |
| 3 | 1 | `lowerFadeSpeed × 10`, or `0x00` if null |
| 4 | 1 | `speed × 10`, coerced to 0.1 … 10.0 |
| 5 | 1 | `lowerSpeed × 10`, or `0x00` if null |
| 6 | 1 | `brightness` (1 … 100) |
| 7 | 1 | `lowerBrightness`, or `0x00` if null |
| 8 | 1 | `colorCount` (1 … 10) `| 0x80` if `randomColorOrder` |
| 9 | 3×N | `colorCount` RGB triplets, `r, g, b` |

`effect` codes (`services/devices/model/LightShowEffect.java`, `f36181a`):
`PULSE=1, FLICKER=2, WAVE=3, ALTERNATING=4, FILL=5, POP=6, STATIC=7, RHYTHM=8, ERRATIC=9,
ROLLING=10, STACKING=11`.

Gated on `Capability.LightShow` (`mo14062y`).

### 4.2 `SetLightShowExtendedCommand` — `F7 11 02 57`

`services/devices/command/SetLightShowExtendedCommand.java` (`m14093Q`) +
`SetLightShowExtendedCommandKt.java` (`m14094a`, `m14095b`). **blockSize 9**, no delay.

**This is a supplement to 4.1, not a replacement** — it carries no effect, brightness, or
colours. `ShowServiceDefault.java:427` constructs `ShowCommands(SetLightShowCommand,
SetLightShowExtendedCommand, SetLightShowTileSpecificParameterCommand?)` and sends them
together.

| Off | Size | Field |
| ---: | ---: | --- |
| 0 | 1 | `showIndex` |
| 1 | 1 | noResult flag, `0x01`/`0x00` — **or a single `0x00` and nothing else if `lightShow == null`** |
| 2 | 2 LE | `fadeSpeed × 100`, coerced to 0.01 … 10.0 |
| 4 | 2 LE | `lowerFadeSpeed × 100`, or `0x0000` if null |
| 6 | 2 LE | `speed × 100`, coerced to 0.0 … 10.0 |
| 8 | 2 LE | `lowerSpeed × 100`, or `0x0000` if null |
| 10 | 3 | `ColorSmoothing`: `{0x01, fadeWidth, colorWidth}` if present, else `{0x00, 0x00, 0x00}` |

Carries a caller-supplied `responseTimeoutMillis` (`getF34900x()`); `ShowServiceDefault` passes
`800L` at lines 1230/1338/1420.

### 4.3 `SetMusicShowCommand` — `F7 11 02 44`

`services/devices/command/SetMusicShowCommand.java` (`m14105Q`) +
`SetMusicShowCommandKt.java` (`m14106a`, `m14107b`). **blockSize 9**, no delay.

| Off | Size | Field |
| ---: | ---: | --- |
| 0 | 1 | `showIndex` |
| 1 | 1 | `effect` code `| 0x80` if noResult — **or a single `0x00` if `musicShow == null`** |
| 2 | 1 | `fadeSpeed × 10` |
| 3 | 1 | `lowerFadeSpeed × 10`, or `0x00` |
| 4 | 1 | `speed × 10` |
| 5 | 1 | `lowerSpeed × 10`, or `0x00` |
| 6 | 1 | `reactiveIntensity` (`SLOW=0`, `FAST=1`), or `0x00` if null |
| 7 | 1 | `reactiveSections`, **default `100` (0x64)** if null |
| 8 | 1 | `brightness` |
| 9 | 1 | `lowerBrightness`, or `0x00` |
| 10 | 1 | `colorCount` `| 0x80` if `randomColorOrder` |
| 11 | 3×N | RGB triplets |

`effect` codes (`services/devices/model/MusicShowEffect.java`):
`PULSE=1, TWINKLE=2, WAVE=3, ALTERNATING=4, FILL=5, RANDOM=6, STATIC=7, RHYTHM=8, ERRATIC=9,
VISUALIZE=10`. Note this differs from `LightShowEffect` at codes 2, 6, 10, 11.

Gated on `Capability.MusicShow`.

### 4.4 `SetMusicShowExtendedCommand` — `F7 11 02 58`

`services/devices/command/SetMusicShowExtendedCommand.java` (`m14108Q`) +
`SetMusicShowExtendedCommandKt.java` (`m14109a`). **blockSize 9**, no delay.
Byte-for-byte the same layout as 4.2 (it even reuses
`SetLightShowExtendedCommandKt.m14095b` for the trailing `ColorSmoothing` block); only the
opcode differs. Also a supplement, not a replacement.

### 4.5 `SetMultiColorBitmapCommand` — `F7 11 02 4F`

`services/devices/command/SetMultiColorBitmapCommand.java` (`m14102Q`) +
`services/devices/model/MultiColorSegmentDataKt.java` (`m14270a`) +
`services/devices/model/SegmentBitmap.java`. **blockSize 9**, no delay.
Both transports build the identical payload (no delegate flag).

| Off | Size | Field |
| ---: | ---: | --- |
| 0 | 1 | `brightness`, or **`0xFF` if null** |
| 1 | 3 | RGB `r, g, b`, or `00 00 00` if colour is null |
| 4 | 1 | `segmentCount` (`SegmentBitmap.f36269a`, must be > 0) |
| 5 | ⌈count/8⌉ | segment bitmap |

Bitmap bit order (`SegmentBitmap.m14276a`, confirmed): segment `n` (1-based) sets
`bytes[(n-1)/8] |= 1 << (7 - ((n-1) % 8))` — **MSB-first within each byte**.

### 4.6 `SaveMultiColorBitmapCommand` — `F7 11 02 56` (+ action byte)

`services/devices/command/SaveMultiColorBitmapCommand.java` (`m14073Q`). The only class in this
set that **extends its opcode array before sending** and **conditionally skips chunking**:

```java
byte[] op = f34629x ++ (byte) action.f34632a;      // F7 11 02 56 <actionCode>
byte[] body = action.writeTo(DataOutputStream);
if (action.f34633b)  DeviceCommand.m14027t(op, body, 8, 0L, send, cont);   // chunked, blockSize 8
else                 send(0, op ++ body);                                  // ONE packet, no framing
```

For the non-chunked actions there is **no block-number byte and no total-count byte** — the
`blockIndex` argument is a literal `0`, so `packet[2]` is `0` too.

| Action | `actionCode` | Chunked | Body |
| --- | --- | --- | --- |
| `Action.Start` | `0x00` | no | `{schemeIndex(1B), isGradientOn(1B, 0/1)}` |
| `Action.SendBitmap` | `segmentBitmap.segmentCount` | **yes**, blockSize 8 | `MultiColorSegmentData` (4B: brightness/0xFF, r, g, b) ‖ bitmap bytes |
| `Action.Complete` | `0xFF` | no | `{transferredSegmentCount(1B)}` |

> **Important caveat — the literals in `Start`/`Complete` are call-site constants, not
> protocol constants.** In this build `Start()` and `Complete()` decompile to no-arg
> constructors with `schemeIndex = 5`, `isGradientOn = false`, `transferredSegmentCount = 6`.
> That is R8 constant-propagation: the **only** construction site in the whole tree is the
> hidden debug screen `debug/p012ui/CoreDebugDevicesFragment$createMultiColorMenuItems$6.java`,
> which runs Start → SendBitmap(`SegmentBitmap(6)` with segments {1,3,5}) → Complete with those
> exact values. The `equals`/`hashCode`/`toString` on both classes still reference the fields,
> so they are genuine parameters in the Kotlin source. **Do not ship 5 and 6 as fixed
> protocol values.** `transferredSegmentCount = 6` matching `SegmentBitmap(6)` is consistent
> with it simply being the segment count.

### 4.7 `SetTileLayoutCommand` — `F7 11 02 53`

`services/devices/command/SetTileLayoutCommand.java` (`m14120Q`) +
`services/devices/model/CustomTileLayout.java`. **blockSize 9**, no delay. Identical payload on
both transports.

| Off | Size | Field |
| ---: | ---: | --- |
| 0 | 1 | `tileCount` (1 … 40) |
| 1 | 1 | `rotation / 30`, must land in `0 … 11` (`STRUCTURE_ANGLE_RANGE`) |
| 2 | tileCount−1 | one `connection` byte per additional tile, each validated to `2 … 6` |

`CustomTileLayout`'s constructor enforces `rotation ∈ 0 … 330` and `rotation % 30 == 0`, and
`connections.size() == tileCount - 1`.

### 4.8 `SetCustomButtonOptionCommand` — `F7 11 02 2C`

`services/devices/command/SetCustomButtonOptionCommand.java` (`m14084Q`) +
`services/devices/model/CustomizableButtonOption.java` +
`services/devices/model/KeypadDimmerButton.java`. **blockSize 9**, no delay.

| Off | Size | Field |
| ---: | ---: | --- |
| 0 | 1 | `button.code` — `TOP=1, SECOND=2, BIG=3` |
| 1 | 1 | `option` type code (table below) |
| 2… | var | type-specific tail |

Option type codes (confirmed, each class's `getF35911b()`):

| Code | Option |
| ---: | --- |
| `0x00` | `Unknown` |
| `0x01` | `DefaultColorTemperatures` |
| `0x02` | `DefaultScenes` |
| `0x03` | `DefaultColors` |
| `0x04` | `CustomScenes` |
| `0x05` | `CustomColors` |
| `0x06` | `OnOffToggle` |
| `0x07` | `On` |
| `0x08` | `Off` |
| `0x09` | `GoogleRoutine` |

Tail:

- `CustomScenes`: `sceneCount(1B)` then one byte per scene id.
- `CustomColors`: `colorCount(1B)` then per colour —
  `RgbColor` → `0x01, r, g, b`; `CctColor` → `0x02, cct`;
  **`RevealColor` → nothing at all is written** (the `else` branch is a bare
  `Intrinsics.areEqual(color, RevealColor.INSTANCE)` with no output). A `RevealColor` in the
  list therefore still increments `colorCount` but contributes zero bytes. Flagging as a
  likely app bug; a reimplementation should not try to reproduce it without a capture.
- All other option types: **nothing** after byte 1.

### 4.9 `SetSameGroupDeviceIdsCommand` — `F7 11 02 61`

`services/devices/command/SetSameGroupDeviceIdsCommand.java` (`m14115Q`). **blockSize 9**, no
delay. Priority is `HIGH` here, not `URGENT` like the rest. Gated on
`Capability.OnOffObserver`.

| Off | Size | Field |
| ---: | ---: | --- |
| 0 | 1 | address count |
| 1… | 1 each | **one byte per address** |

The writer is `dataOutputStream.write(meshAddress.f31064a & 0xFFFF)`. `OutputStream.write(int)`
writes **only the low 8 bits**, so the high byte of the 16-bit mesh address never reaches the
wire. Two readings are consistent with the evidence:

1. **Intentional (favoured).** `MeshAddress` treats the low byte as the *device number within a
   group* and the high byte as the *group number* — see `MeshAddress.java` lines 100-101
   (`f31064a & 255` when device-or-group) and 121-122 (`>> 8` for the group). The command is
   named "SameGroupDeviceIds" and is gated on `OnOffObserver`, so sending group-relative device
   ids is coherent. The `& 0xFFFF` is then just unsigned-widening of a `short`, with the
   truncation being an accepted side effect.
2. **A truncation bug.** Device addresses are validated only against `1 … 32767`
   (`MeshAddress.m13637g()`), so an address ≥ 256 would be silently mangled. Contrast
   `SetMeshAddressCommand`, which uses the explicit 2-byte writer `ExtensionsKt.m13359f`.

Distinguishing these needs a capture with a device whose mesh address exceeds `0x00FF`.

### 4.10 `SetMeshAddressCommand` — `E0 11 02`

`services/devices/command/SetMeshAddressCommand.java`.

- **Not chunked.** `mo14012f` builds one body and calls
  `telinkCommandDelegate.mo14046d(body, meshAddress, 0, cont)` directly — `packet[2] = 0`.
- **No Xlink path at all** (**confirmed**): the class overrides only `mo14012f`;
  `UnitDeviceCommand` (`services/devices/command/UnitDeviceCommand.java`) does not override
  `mo14013g`, and the base `DeviceCommand.mo14013g` throws `UnsupportedOperationException`.

Body:

| Off | Size | Field |
| ---: | ---: | --- |
| 0 | 3 | `E0 11 02` |
| 3 | 2 LE | new mesh address (`ExtensionsKt.m13359f`: low byte then high byte) |

Total 5 bytes. Constructor rejects anything that is not `MeshAddress(0)` (the self/unprovisioned
sentinel) or a device address.

Also sets `this.f34466c = 3`. In `TelinkDeviceBleManager.m14326L` that field becomes the retry
budget (`retryLimit = value + 1`), so this command gets 4 attempts vs. the default 3.
ConnectionType is `DeviceCommand.f34459i` = **`{BLE}` only**.

### 4.11 `SetWifiCommand` — `F6 11 02 02`

`services/devices/command/SetWifiCommand.java`. Already carries a detailed inline
`[cync-lan reverse-engineering note]`; independently re-verified here against
`com/gelighting/cbygekit/foundation/Utilities.java` (`m13402k`) and
`Utilities$chunkByteArray$1.java`, and confirmed accurate.

- **Does not use `sendBlocks`.** It has its own chunker.
- **No Xlink path** — only `mo14060M` is overridden;
  `StatusNotificationQueryCommand.mo14023N` (line 652) throws `UnsupportedOperationException`.
- ConnectionType `{BLE}` only. Response timeout 30 s (`getF34554w()`).

Inner stream, built before chunking:

```
[ ceil((len(ssid) + len(pass) + 5) / 8) ]   // total chunk count
[ len(ssid) ] ssid(UTF-8)
[ len(pass) ] pass(UTF-8)
[ 0x01 ]
[ deviceType.id ]
```

(stream length is exactly `len(ssid) + len(pass) + 5`, so the count byte is self-consistent.)

Then `CollectionsKt.chunked(stream, 8, chunkByteArray$1)`, where the lambda prefixes each
8-byte-or-fewer chunk with a **1-based** running index. Each chunk is sent as:

```
commandBody = F6 11 02 02 ‖ chunkIndex(1-based) ‖ chunk(≤8B)
mo14046d(commandBody, meshAddress, chunk[0] /* = chunkIndex */, …)
```

**Divergence worth noting:** here `packet[2]` receives the **1-based** index (`byte b = bArr[0]`),
whereas `sendBlocks` passes the **0-based** index. Both schemes put a 1-based number inside the
body. Confirmed by reading both call sites side by side.

---

## Not found / open

- **`BLOCK_SIZE` as a named constant.** `@Metadata` proves each Companion declares `BLOCK_SIZE`
  (and `OPCODE_BYTES`), but R8 inlined the value, so `9`/`8` are read from the `m14027t` call
  arguments rather than from a named field. Values are certain; the name binding is
  `@Metadata`-level evidence only.
- **Semantics of the `noResult` flag.** Plausible only — see §3.
- **Whether `SetSameGroupDeviceIdsCommand`'s single-byte-per-address is intentional.** See §4.9.
- **`SaveMultiColorBitmapCommand`'s `schemeIndex` / `transferredSegmentCount` value ranges.**
  The only constructions in the tree are the debug screen's hardcoded 5 and 6. Real ranges not
  established.
- **Whether any device actually rejects a chunked `0x8E`-wrapped Xlink send.** Every chunked
  command here inherits the 0x8E-outer-opcode behaviour, but no capture in this tree exercises
  a chunked command over the Xlink path — only single-shot ones (the plug power-toggle cited on
  `XlinkDeviceManager.mo14056h`). Whether multi-block reassembly even works across that relay
  is untested.
- **`delayMillis` is 0 for every command in this set.** No command found that passes a nonzero
  inter-block delay, so the delay branch of `sendBlocks` is confirmed present but unexercised
  here.
