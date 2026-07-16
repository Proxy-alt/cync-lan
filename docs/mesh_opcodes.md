# Mesh command opcodes

This documents the *inner* BTLE-mesh command layer — the `op_code`/`cmd_code`/`sub_id`/payload
that travels inside a `0x73` data-channel packet (see [packet_structure.md](packet_structure.md)
for the outer TCP framing that wraps it). Everything here follows the same envelope pattern
cync-lan already uses in `src/cync_lan/devices.py`: `send_command(op, cmd_, sub_id, payload, ...)`,
built by `PacketBuilder.build_control_packet()` (`src/cync_lan/packet/builder.py:205`).

Sourcing conventions used throughout this doc, matching the existing standard set by
`devices.py`'s `_build_motion_sensor_settings_payload` docstring:
- **confirmed** — cited to an exact decompiled-app class/line, or proven from a real packet
  capture, or already shipping in production.
- **plausible** — a reasonable inference from decompiled evidence, not directly proven.
- **not found / blocked** — explicitly flagged as absent, not guessed at.

Research credited to background agents run 2026-07-16 against
`/Users/proxy-alt/Downloads/cync_decompiled/`, cross-referenced with this repo's own
`src/cync_lan/devices.py`.

## The `cmd_code` mystery — resolved, see "TCP relay envelope research" below

Every *new* command researched below hit the same wall: the decompiled Android app's BTLE-mesh
command classes (`com/gelighting/cbygekit/services/devices/command/*.java`) only ever expose a
3-4 byte **inner mesh opcode array** (e.g. `{0xF7, 0x11, 0x02, 0x07}` for motion-sensor settings) —
there is no field in that layer corresponding to cync-lan's own `cmd_code`.

`PacketBuilder.build_control_packet()` shows where `cmd_code` actually lives structurally:

```
header  = [msg_id][0x00 0x00 0x00][0xF8][op_code][cmd_code][0x00]   # 8 bytes
routing = [msg_id][0x00 0x00 0x00 0x00][target_id][sub_id]           # 7 bytes
inner   = header + routing + [op_code] + command_payload
```

`cmd_code` sits immediately after `op_code` in cync-lan's own inner-packet header — this is
**cync-lan's own wrapper framing**, not part of the BTLE mesh command itself. That's consistent
with why the phone app's BTLE-mesh command classes never mention it: the app talks BTLE GATT
directly to a device; cync-lan (and, presumably, the real WiFi-bridge firmware) instead relays
mesh commands over TCP, and `cmd_code` is very likely specific to *that* relay/tunneling layer,
not the mesh command payload.

**Resolved** — see "TCP relay envelope research" immediately below: `cmd_code` is a payload-length
field, not a semantic command code. The `set_brightness`/`set_temperature`/`set_rgb` overlap at
`cmd_code = 0x10` isn't a "response category" as originally hypothesized here — it's simply that
all three happen to share the same 8-byte payload length. Left the original hypothesis text out of
this doc entirely rather than keeping a disproven guess around; see the section below for the
actual mechanism and formula.

## TCP relay envelope research

Found the app's TCP-relay outer-envelope builder the earlier BTLE-only search missed: it lives
under `com/gelighting/cbygekit/services/devices/xlink/` (the "xlink" IoT-relay SDK layer, not the
`services/devices/command/` BTLE package). `XlinkDeviceManager.CommandDelegate` implements
`XlinkCommandDelegate` (`XlinkCommandDelegate.java:49-61`, methods `f`/`g`/`h`) and is what every
command class ultimately calls (`ControlDeviceGroupCommand.java:159`,
`SetComboCommand.java:130`, etc., via `xlinkCommandDelegate.f((byte) op_code, commandBody,
meshAddress, ...)` — the `byte` argument here is confirmed to literally be `op_code`: `-41`=`0xD7`
for groups, `-16`=`0xF0` for `SetComboCommand`, matching the doc's confirmed table exactly).

`CommandDelegate.f()` (`XlinkDeviceManager.java:1010-1026`) prepends a 7-byte routing prefix
(3-byte msgId LE + 2 zero bytes + 2-byte destination `MeshAddress` LE) to `commandBody`
(op_code byte + sub-opcode/payload — the same array already documented above), then calls
`g()` → `Xlink.a(byte op_code, byte[] data, int msgId)` (`Xlink.java:23-70`), which builds:
`[msgId(4B LE)][0xF8][op_code(1B)][length(2B LE)][data][checksum]`, then 0x7E-delimits and
byte-stuffs it (0x7D/0x7E escaping) — an HDLC-style framing, **confirmed** distinct from
cync-lan's own captured 5-byte-header wire format in `packet_structure.md` (no delimiters/
escaping there). The `0xF8` marker is a real constant (`com.thingclips.sdk.bluetooth.pdqbbbp.
dpdqppp = 248`), and this whole `io.xlink.wifi.sdk`/`xlink.legacy` pathway carries a `@Deprecated`
tag on its writer thread (`TcpPacketWriter.java:13`) — so this is very likely the phone-app's
older command channel, not necessarily byte-identical to the device-facing protocol cync-lan
replicates. Flagging as **plausible**, not confirmed, for that reason.

**The payoff**: the 2 bytes right after `op_code` in this header are not a semantic field at all —
they're `WriteBuffer.d(length)` (`xlink/legacy/WriteBuffer.java:41-44`), the little-endian **byte
length of `data`** (7-byte routing prefix + `commandBody`). Testing `cmd_code = 7 + len(commandBody)`
against all three already-confirmed production values below reproduces them exactly:
`set_power`: 7 + (1 op_code + 5B payload) = 13 = `0x0D` ✓. `set_brightness`/`set_rgb`: 7 + (1 + 8B)
= 16 = `0x10` ✓. `set_lightshow`: 7 + (1 + 6B) = 14 = `0x0E` ✓. The doc's fixed trailing `0x00` byte
is simply the length field's high byte, zero because no mesh payload is anywhere near 256 bytes.
This gives a directly testable formula for every "blocked" command above (e.g. scenes `0xEF`:
7+(1+4B `[0x11,0x02,sceneId,0x01]`)=`0x0C`; fine-brightness `0xE2`: 7+(1+7B)=`0x0F`) — cheap to
verify against a live capture, much stronger than a blind guess. Apply this formula to every
command flagged "blocked: `cmd_code`" below before assuming a capture is required.

## Confirmed, already shipping in production

Extracted directly from `src/cync_lan/devices.py` (not decompiled-app-sourced — these already
work against real hardware).

| Command | `op_code` | `cmd_code` | Payload shape | Source |
|---|---|---|---|---|
| `set_power` | `0xD0` | `0x0D` | `[0x11, 0x02, state, 0x00, 0x00]` (5B) | `devices.py:770` |
| `set_brightness` | `0xF0` (`0xD2` if sol-lamp) | `0x10` | non-sol: `[0x11,0x02,0x01,bri,0xFF,0xFF,0xFF,0xFF]` (8B); sol: `[0x11,0x02,bri,0x00,0x00]` (5B) | `devices.py:790` |
| `set_temperature` | `0xF0` (`0xE2` if sol-lamp) | `0x10` | non-sol: `[0x11,0x02,0x01,0xFF,temp,0x00,0x00,0x00]` (8B); sol: `[0x11,0x02,0x05,temp,0x00]` (5B) | `devices.py:817` |
| `set_rgb` | `0xF0` | `0x10` | `[0x11,0x02,0x01,0xFF,0xFE,r,g,b]` (8B) | `devices.py:844` |
| `set_lightshow` (factory presets only) | `0xE2` | `0x0E` | `[0x11,0x02,0x07,0x01,byte1,byte2]` (6B) — `0x07` = light-run-mode sub-cmd, `0x01` = hardcoded `MODE_LIGHT_SHOW` | `devices.py:870` |

Note the `cmd_code = 0x10` overlap across brightness/temperature/rgb despite three different
`op_code`s — explained by the length-field formula in "TCP relay envelope research" above
(all three share the same 8-byte payload length), not a semantic coincidence.

## Provenance of already-confirmed cmd_code values

Mystery solved for all five: every `cmd_code` in the table above traces to a **real socat-MITM
packet capture**, not decompiled-app inference — the decompiled-app search came up empty precisely
because `cmd_code` genuinely isn't in that layer (per "The `cmd_code` mystery" above); it was only
ever knowable from a live capture, and one was already done, years before this doc.

- **`set_power` = `0x0D`** — confirmed: a full socat session log,
  `docs/debugging_sessions/3 devices/Plug - Toggle Power/{Plug,App}.md` (captured 2024/03/11),
  shows six raw power-toggle packets from the real Cync Android app, e.g. `Plug.md:255`:
  `... f8 d0 0d 00 21 00 00 00 00 05 00 d0 11 02 01 00 00 e7 ...` — `f8` then `d0` (`op_code`) then
  `0d` (`cmd_code`), matching `PacketBuilder.build_control_packet()`'s header layout exactly.
- **`set_brightness`/`set_temperature`/`set_rgb` = `0x10`** — confirmed: raw hex dumps embedded as
  docstring comments in pre-refactor `devices.py`, each annotated with the sender's own checksum
  arithmetic (proving they're real captures, not authored examples), all showing `f8 f0 10 ...`:
  `set_brightness` at commit `8e9623a:src/cync_lan/devices.py:405-407`
  (`f8 f0 10 00 17 ... f0 11 02 01 27 ff ff ff ff 45`); `set_temperature` at
  `8e9623a:src/cync_lan/devices.py:503-506`; `set_rgb` at
  `7cd035c:src/cync_lan/devices.py:693-697` (`f8 f0 10 00 2b ... f0 11 02 01 ff fe 00 fb ff`,
  checksum verified as `2d`). Commit `7cd035c` ("Merge pull request #31 from tobyroworth/device-151",
  2026-05-17) is the last commit before the `096b735` "proxy/mitm" refactor stripped these comments
  when reformatting these commands onto the current `op`/`cmd_`/`send_command()` pattern.
- **`set_lightshow` = `0x0E`** — confirmed: same pre-refactor file,
  `7cd035c:src/cync_lan/devices.py:794-841`, ten separate raw captures (one per factory effect:
  candle, rainbow, cyber, fireworks, volcanic, aurora, happy holidays, red-white-blue, vegas, party
  time), all `f8 e2 0e ...`.

Upstream lead, not needed given the above: this repo's `python` branch was itself imported via
commit `8e9623a` ("initial import of lib code from HASS with touch ups") from a HASS custom
component predecessor; `git remote -v` shows `upstream -> baudneo/cync-lan`, and `README.md`
credits a further-upstream fork chain (`iburistu/cync-lan`, `juanboro/cync2mqtt`) — the original
capture sessions likely predate this repo entirely, but the evidence already in-tree above is
sufficient on its own.

Scaffolded but **not wired to a live send** (blocked on `cmd_code`):

| Command | `op_code` (confirmed) | `cmd_code` | Payload shape (confirmed) | Source |
|---|---|---|---|---|
| Motion/ambient-light sensor settings | `0xF7` | **unconfirmed** | `[type_discriminator(1=motion,2=ambient), enabled, sensitivity, delay_s, deactivation_s, ...]` (8B params after opcode) | `devices.py:895`, decompiled: `SetMotionSensorSettingsCommand.java` opcode array `{-9,17,2,7}`, cross-checked twice |

## New findings, not yet in cync-lan (all blocked on `cmd_code` unless noted)

### Fine/fade brightness — `op_code = 0xE2`, sub-command `0x08`

Extends the same command family `set_lightshow` already uses (`0xE2` outer, `0x11 0x02` prefix).

- Payload after `[0x11, 0x02, 0x08]`: `brightness × 10` as **big-endian u16** (0–1000, i.e. tenths
  of a percent) + fade duration in **milliseconds** as **big-endian u16** (max ~65.5s).
- HA's `light.turn_on(transition=...)` (seconds) maps directly: `fade_ms = round(transition * 1000)`.
- Confirmed: `SetFineBrightnessCommand.java` line 49 (`f20525r = {-30,17,2,8}`), payload builder
  `x()` lines 120-129, `writeShort` calls read directly (no decompiler ambiguity on byte layout).
- Blocked: `cmd_code`, same as everything else.
- Adjacent, unrelated: `SetBrightnessCommand.java` (`{-46,17,2}` = `0xD2 0x11 0x02`, plain 0-100
  int, no fade) — a *different*, coarser opcode family, not needed since cync-lan's existing
  `set_brightness` already works.

### Full light-run-mode incl. MultiColor/MusicShow — `op_code = 0xE2`, sub-command `0x07`

**This is the one item in this doc that's actually buildable today without any capture** — it's a
small parameterization of `set_lightshow`, which already has a confirmed-working `cmd_code`.

Payload after `[0x11, 0x02, 0x07]` is `[modeCode, index, randomNonce]` — cync-lan's current
`set_lightshow` hardcodes `modeCode = 0x01` (`LIGHT_SHOW`). Confirmed mode values
(`LightRunMode.java` lines 55-59, each with a `super(N)` call fixing its constant):

| `modeCode` | Mode | Index range | Notes |
|---|---|---|---|
| `0x00` | Static | always `0` | |
| `0x01` | LightShow | 1-9, 65-67 (factory), 10-32 (custom) | cync-lan's current only mode, matches existing `FACTORY_EFFECTS_BYTES` |
| `0x02` | MusicShow | 1-8, 65 (factory), 10-32 (custom) | **Not raw audio data** — device does audio-reactivity locally via its own mic; the wire command just selects a preset index, identical mechanism to LightShow |
| `0x03` | Reveal | always `0` | |
| `0x04` | MultiColor | 1-2 (factory), 3-32 (custom) | *activating* a saved scheme only — see below for uploading custom scheme data |

Source: `SetLightRunModeCommand.java` (opcode `q = {-30,17,2,7}`), `x()` lines 108-126;
`LightShow.java`, `MusicShow.java`, `Reveal.java`, `MultiColor.java` (index-range constants via
`super(N)`/`IntRange(...)`).

**Custom MultiColor scheme creation** (uploading arbitrary per-segment RGB data, not just
activating a saved one) is a **separate, unrelated opcode**: `SetMultiColorSegmentsCommand.java`,
`{0xF7, 0x11, 0x02, 0x4E}` — payload is a gradient-mode toggle, segment count, or up to 2 segments
per packet as `[position_or_255, R, G, B]`. Also `SetMultiColorSchemeDirectCommand.java` (xlink
`0x89`, "entertainment"/live-streaming variant) and `SetMultiColorBitmapCommand.java` (unread,
likely tile/matrix bitmap data for other product types). Out of scope for a `set_lightshow`
extension — a materially bigger feature with its own data model.

Related for a future scene-export feature: `AddDeviceSceneCommand.java` (`{-18,17,2}` =
`0xEE 0x11 0x02`) documents the 6-byte per-device state block scenes store
(`mode, brightness/param, color-temp-or-254-for-RGB, R, G, B`).

Also relevant: `SetLightRunModeUseCase.java` shows the app validates custom show/scheme existence
via the cloud before sending, and for multi-device groups allocates a temporary index (129-255)
via `SetShowTemporaryMappingCommand` rather than sending the raw custom index directly — relevant
if cync-lan ever supports custom (non-factory) shows/schemes on groups specifically.

### Groups control — `op_code = 0xD7`

**Not what the name suggests.** This is mesh **group-membership management** (a device
subscribing/unsubscribing to a group's pub/sub address) — not "control an entire group's state in
one packet."

- Confirmed: `ControlDeviceGroupCommand.java` line 120, `f20280s = {-41,17,2}` = `{0xD7,0x11,0x02}`.
  Base class for `AddDeviceGroupCommand`/`RemoveDeviceGroupCommand`. Payload (`x()` lines 190-214):
  `action` byte (ADD=1/REMOVE=0) + 2-byte **little-endian** group address (`ExtensionsKt.f`,
  confirmed low-byte-first at `ExtensionsKt.java:83-90`) + optional `GroupReachFlag` byte
  (RX=0x87, RXTX=0x00).
- No other occurrence of opcode `0xD7` anywhere in `com/gelighting/cbygekit` (verified by grep).

**The actual "control a whole group at once" mechanism — plausible, not directly confirmed:**
`MeshAddress.java` confirms cync-lan's own group-address assumption exactly:
`GROUP_ADDRESS_RANGE = 32768 until 65535` (line 42), `groupId = address - 32768` (`f()`,
lines 146-151). `SendCommandToDeviceGroupsUseCase.java` has a `DeviceGroup.Group` variant
(alongside `IndividualDevice`/`IndividualDevices`) suggesting the app dispatches its *ordinary*
per-device commands — the same `0xD0`/`0xF0`/etc. cync-lan already implements — targeted at a
group's synthetic `MeshAddress` (32768+groupId) rather than a device address. The concrete
resolution call site was abstracted through `Result<Flow<T>>` in the decompiled source and
couldn't be traced to full confirmation.

**If this holds, group control needs zero new opcodes and no `cmd_code` capture at all** — just a
lightweight target object with `.id = 32768 + group_id` passed through the *already-working*
`set_power`/`set_brightness`/etc. `send_command()` already takes `target_id=self.id` as a plain
int field (`devices.py:678`), so this is a cheap, low-risk thing to test live against a real
group before investing in anything else here.

### Scenes control — `op_code = 0xEF`

- Confirmed: `ExecuteSceneCommand.java` line 54, `f20351p = {-17,17,2}` = `{0xEF,0x11,0x02}`.
  Payload (`x()` lines 198-207): `[sceneId(1 byte, 0-255), 0x01]`. Scenes are scoped **per-home**
  (`Location`), not per-group (`SceneModel.java` line 96: `SceneId(id, locationId)`).
- The `0x1E` byte from the original ask is real but belongs to a **separate legacy dispatch path**
  for non-mesh device types (`ExecuteSceneCommand.g()` line 179,
  `xlinkCommandDelegate.g((byte) 30, ...)`) — a distinct xlink call, not confirmed to be cync-lan's
  `cmd_code`. Treat as a separate legacy opcode, not part of the mesh-command family above.
- Blocked: `cmd_code` for the mesh-family version.

### Indicator LED ring — `op_code = 0xF7`, sub-command `0x06`

Same opcode family as motion-sensor settings (`0xF7 0x11 0x02`), sibling sub-command.

- Confirmed: `SetStatusIndicatorSettingsCommand.java` (`OPCODE_BYTES = {-9,17,2,6}`),
  `StatusIndicatorSettings.java`, `LEDIndicatorMode.java`/`LEDIndicatorColor.java`. Payload builder
  `Q()`: `[(mode<<4)|color, brightness(1-100), wifi_disconnect_flag(0/1)]` after the 4-byte opcode.
  `LEDIndicatorMode`: ALWAYS_ON=0, ALWAYS_OFF=1, NORMAL=2. `LEDIndicatorColor`: WHITE=0, RED=1,
  GREEN=2, BLUE=3 (a 4-value enum, not full RGB).
- "WiFi-disconnect toggle" is **not a separate feature** — it's byte index 3 of this same payload
  (blink-on-disconnect flag for the indicator LED), not a device behavior setting for what happens
  functionally when WiFi drops. No evidence anywhere in the decompile of an actual
  network-loss-behavior command.
- Blocked: `cmd_code`.
- Both this command and motion-sensor settings were traced down into the app's shared BTLE
  delegate interface (`XlinkCommandDelegate.java`, `h(byte[] payload, MeshAddress, msgId, msgId2,
  Continuation)`) — confirmed that layer carries no field resembling `cmd_code` either. Reinforces
  the "TCP relay-specific, not mesh-layer" theory above, but see the "not yet checked" note in that
  section — this was the BTLE GATT delegate specifically, not any TCP/cloud-relay equivalent.
- Worth a follow-up read: `com/savantsystems/oneapp/domain/devices/model/Component.java:2845`
  (`LightRingIndicator`) — a second, UI-level mode/brightness/color enum
  (`LightRingIndicatorMode`) distinct from `LEDIndicatorMode`, translated via
  `LightRingIndicatorModeToLEDIndicatorModeMapper` — unclear if it maps 1:1 or adds states.

### Motion-sensor schedule write — `op_code = 0xF7`, sub-command `0x0B`

Third sibling in the `0xF7 0x11 0x02` family (alongside motion/ambient settings at `0x07` and
indicator LED at `0x06`). Writes one of a group's 4 fixed motion-sensor schedule slots — see
"Cync-native automations (scenes, schedules, motion-sensor schedules)" below for the full data
model this command writes.

- Confirmed: `SetMotionSensorScheduleCommand.java` lines 85-193, `OPCODE_BYTES = {0xF7,0x11,0x02,0x0B}`.
  Payload after the 4-byte opcode: a flags byte (slot id 0-3 packed with mode bits and an
  RGB-vs-CCT flag), start hour, start minute, end hour, end minute, brightness, then either a CCT
  byte or 3 RGB bytes depending on the flag.
- Blocked: `cmd_code` — apply the length formula from "TCP relay envelope research" above the same
  as any other command here (payload length is confirmed, so this one's actually computable, not
  just theoretically so).
- **This is the one write-side finding in this doc that doesn't need a cloud API at all** — it's a
  local mesh command, architecturally identical to every other opcode cync-lan already speaks. See
  the automations doc for why that matters for a HA-automation-to-Cync-device sync feature.

### Multi-way-mode diagnostic — no wire opcode exists

Not a protocol command at all. `MultiWayMode.java` is a plain boolean
`SimpleDeviceSpecificProperty`; `SetMultiWayModeGeCommandHandler.java` only mutates the in-memory
cloud `DeviceModel` and notifies `SceneService` if enabling — no BTLE/TCP write is built anywhere.
Confirmed via `OperationManager.java:840-845` and `MappersKt.java:815-823`: it round-trips purely
through `LocationSnapshot`/`DeviceItem` cloud serialization (`deviceItem.V`), set at commissioning
time (`CommissioningMultiWayModeFragment`, `MultiWayModeFragment`). If cync-lan wants this, it
would need to come from the cloud export data, not a device packet — worth checking whether
`cloud_api.py`'s existing export already captures this field under some `deviceItem` key.

## Device type coverage — closed

Audited against `DeviceType.java`'s sealed-class registry (155 real numeric IDs, the app's own
`deviceTypeByValue` companion-object map — confirmed authoritative, it's literally what the real
app uses to resolve a cloud `deviceType` int). All 155 are already keys in
`src/cync_lan/metadata/model_info.py`'s `device_type_map` (156 keys total; the one extra,
`85` = "Tunable White Light (Unknown)", appears to be from a real capture rather than the app's
static enum, not a gap). Camera types (240/241/242) are present and correctly marked
`UNKNOWN`/`supported=False` — different transport, intentionally out of scope. No further work
needed here unless a newer app build adds types.

## Open threads for future research

1. ~~Find the app's TCP/cloud-relay outer-envelope builder~~ — **done**, see "TCP relay envelope
   research" above: `cmd_code = 7 + len(op_code_byte + full_payload)`, verified 3/3 against
   already-confirmed production values. Not yet independently verified against a live capture
   (the source class is `@Deprecated` in the app, flagged plausible not confirmed) — that's now
   the highest-value remaining step, since it would validate every "blocked" command in this doc
   at once rather than one at a time.
2. **Wire up every "blocked: `cmd_code`" command above using the formula from step 1**, then
   confirm against a live device (motion/ambient sensor tuning, indicator LED, scenes, fine/fade
   brightness, groups-if-the-address-targeting-theory-doesn't-pan-out). This is now a
   plug-in-the-numbers task, not a research task.
3. **Test the group-address-targeting hypothesis live** — cheapest, highest-value next step,
   no `cmd_code` involved at all either way (see Groups section above).
4. A real packet capture (MITM of a device's TCP session, which cync-lan's DNS-redirect setup
   already positions for) remains the way to fully confirm the length-field formula above, and
   the only path for anything that formula's `@Deprecated`-flagged source class turns out not to
   predict correctly.

## BTLE mesh provisioning & MeshInfo details

**MeshInfo request pagination — confirmed, and resolves one `cmd_code` for free.**
`QueryMeshStatusCommand.java` (opcode `82` = `0x52`, sent via
`xlinkCommandDelegate.g((byte) 82, ...)`) builds a 6-byte payload: 2 reserved zero bytes + `total`
(2B little-endian, `-1`/`0xFFFF` = "all devices") + `offset` (2B little-endian, pagination start
index). This matches cync-lan's `build_mesh_info_request` payload byte-for-byte
(`00 00 FF FF 00 00` after `F8 52 06`) — confirming `cmd_code = 0x06` for this command specifically,
and that a targeted re-query (fewer devices, or resuming at an offset) is possible by varying
`total`/`offset` instead of always requesting the full mesh.

**24-byte MeshInfo entry struct — cross-confirmed, plus one unparsed field found.**
`MeshStatusNotification.XlinkParsers.DeviceStatusPagesParser.d()` (the "WifiProxy"/bridge-relay
parser variant, as opposed to sibling method `.c()` for BTLE-hub-relayed reports, which uses
different offsets and isn't cync-lan's path) reads: address `dataBytes.d(0)` (2-byte short, not the
1-byte `dev_id` cync-lan currently reads — safe for base addresses 1-255 but would truncate a
nonzero high "element ID" byte, see below), a boolean flag at **byte 3** (not currently parsed by
cync-lan — feeds into the on/off determination alongside byte 8), power state at byte 8, brightness
at byte 12, color-mode/temp discriminator at byte 16, and RGB at byte 20-22 — all exactly matching
`_process_73_mesh_info`'s existing `dev_state/dev_bri/dev_tmp/dev_r,g,b` offsets in `devices.py:2141-2146`.

**Mesh addressing — confirmed, broader than the group-range note already in this doc.**
`MeshAddress.java`: a `MeshAddress` is one 16-bit value = `base_address | (element_id << 8)`
(`Companion.a()`, ~line 60), where `base_address` is 1-254 (`DEVICE_BASE_ADDRESS_RANGE`) and
`element_id` is 0-126 (`DEVICE_ELEMENT_ID_RANGE`, 0 = "no sub-element"). This means multi-gang
devices can be individually addressed via the address's high byte, a second mechanism alongside the
brightness-byte bitmask cync-lan already uses for `MULTI_ENDPOINT_TYPES`/type 67 — not currently
exercised by cync-lan, but relevant if a future device type needs per-gang targeting. Also confirmed:
broadcast address = `0xFFFF` (`MeshAddress.f` = 65535) and `0x0000` (`MeshAddress.f18318g`) is a
"none/self/unassigned" sentinel. `GROUP_ADDRESS_RANGE` (32768-65535) matches this doc's existing
Groups section exactly.

**Provisioning/commissioning — confirmed to be WiFi-credential handoff, not BTLE mesh key exchange.**
`GECommissioningDataSource.g()` builds each device's commission record via
`SetWifiResponseModel(ssid, mac, encryptionType, ...)` — i.e. "add device" hands the device the
home's WiFi SSID/password, it's the device firmware that joins the WiFi/mesh, not the app running a
Bluetooth-SIG mesh provisioning exchange. **Not found**: no `NetworkKeyCommand`/`PairingCommand`/
`MeshProvisioner` equivalent exists under `com/gelighting/cbygekit/` — those class names only exist
under `com/thingclips/sdk/{bluetooth,sigmesh}` (Tuya/ThingClips SDK bundled for unrelated product
lines, not reachable from GE/Cync's `GECommissioningDataSource`). `CommissionBuilder.java` (179
lines, fully read) only manages cloud-side Location/Group/Subgroup placement — no mesh-address
allocation logic found in the app; address assignment for a newly joined device appears to happen
in bridge/device firmware, outside anything the decompiled app source can confirm.
