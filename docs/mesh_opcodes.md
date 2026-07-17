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

**UPDATE**: wired into a real, EXPERIMENTAL send as of this session's later work (see below) - the
predicted `cmd_code = 0x13` (corrected from an earlier miscounted 9-byte/lower-value estimate; the
real payload is 11 bytes, `">BBBHHB"` is 8 bytes not 6). Exposed as the
`cync_lan.experimental_set_motion_sensor_settings` HA service.

**UPDATE 2, this session**: the `0xF7` below is **not** cync-lan's outer `op_code` - see the
"CORRECTION" section further down. The real outer op is `0x8E`; `0xF7` is the payload's own
leading discriminator byte. Fixed in `devices.py`; `cmd_code` is unchanged (`0x13`).

| Command | `op_code` (confirmed) | `cmd_code` | Payload shape (confirmed) | Source |
|---|---|---|---|---|
| Motion/ambient-light sensor settings | `0x8E` (payload leads with `0xF7`, see CORRECTION below) | `0x13` (**predicted**, not confirmed against a live capture) | `[0xF7, 0x11, 0x02, 0x07, type_discriminator(1=motion,2=ambient), enabled, sensitivity, delay_s, deactivation_s, ...]` (12B total, incl. the leading `0xF7` discriminator now sent as payload) | `devices.py`'s `_build_motion_sensor_settings_payload`/`set_motion_sensor_settings`, decompiled: `SetMotionSensorSettingsCommand.java` opcode array `{-9,17,2,7}`, cross-checked twice |

## Protocol commands beyond the original confirmed set

**UPDATE**: the "TCP relay envelope research" section above resolved the `cmd_code` mystery with a
length formula, validated 3/3 against production commands. Fine/fade brightness, indicator LED,
scenes, and motion/ambient sensor settings (below) are now wired into real, EXPERIMENTAL sends
using `cmd_code` values *predicted* by that formula - not independently confirmed against a live
capture. Each is exposed as a `cync_lan.experimental_*` HA service (see
`custom_components/cync_lan/services.py`), named and documented as experimental so a wrong
prediction is easy to diagnose and doesn't look like a normal confirmed feature. Light-run-mode
(below) needed no such caveat - it reuses `set_lightshow`'s already-confirmed `cmd_code`.

### CORRECTION (real-hardware test, this session): the `0xF7`/`0xEF` "op_code" for 3 commands was never a real outer op

**Update: the fix below is confirmed working** - the user re-tested `set_indicator_led` after this
correction and it now works on real hardware. See the "Indicator LED ring" section further down.

A real-hardware test of `set_indicator_led` (the "ring light" feature) came back a total no-op -
no error, device did nothing (fire-and-forget, no ACK checked, so a wrong guess just silently
vanishes). Root cause, traced by a background research agent digging into the decompiled app's
actual send path: `SetStatusIndicatorSettingsCommand`, `SetMotionSensorSettingsCommand`, and
`ExecuteSceneCommand` **do not pass their "opcode array"'s first byte as an outer op_code at all**.
Their `N()`/send method calls `XlinkCommandDelegate.DefaultImpls.c(...)` with the *entire* opcode
array as one opaque `commandBody` (no separate op argument) → `XlinkDeviceManager.CommandDelegate.h()`
(`XlinkDeviceManager.java:1050-1051`) hardcodes the real outer op: `f((byte) -114, bArr, ...)` =
**`op_code = 0x8E`**, a generic "mesh-relay" op shared across all three commands (and likely others -
`SetMotionSensorScheduleCommand.java:129-130` routes through the identical path). What we'd been
reading as "`op_code = 0xF7`/`0xEF`, payload starts `0x11 0x02 ...`" is actually "`op_code = 0x8E`,
payload starts `0xF7`/`0xEF` (the array's real first byte) `0x11 0x02 ...`" - the array's first byte
was never our envelope's op, it's the payload's own leading discriminator byte.

**Independently confirmed**, not just from static decompiled-source tracing: a genuine captured
packet, `docs/debugging_sessions/3 devices/Plug - Toggle Power/Plug.md:226`
(`f8 8e 0b 00 20 00 00 00 00 ff ff f7 11 02 21 e2`), decodes byte-for-byte against
`PacketBuilder.build_control_packet(msg_id=0x20, target_id=0xFF, sub_id=0xFF, op_code=0x8E,
cmd_code=0x0B, command_payload=[0xF7,0x11,0x02,0x21], repeat_op_code=False)` - checksum included
(verified: `sum(0x8e,0x0b,0x00,0x20,0x00,0x00,0x00,0x00,0xff,0xff,0xf7,0x11,0x02,0x21) % 256 ==
0xe2`, matching the captured checksum exactly). This is a genuine, different real command (a plug
power toggle, not indicator LED) that happens to share the same `0x8E` op family with a
`0xF7 0x11 0x02`-prefixed payload - strong evidence `0x8E` is real, shared infrastructure, not
specific to one feature.

**A second, structural bug this exposed**: `PacketBuilder.build_control_packet()` unconditionally
inserted a repeated standalone `op_code` byte between the routing section and the payload - true
for every op family confirmed so far (`0xD0`/`0xF0`/`0xE2`), but the real capture above only
balances its checksum with **no** such byte for the `0x8E` family. Added a `repeat_op_code: bool =
True` parameter (default preserves all existing confirmed commands unchanged) - `False` for `0x8E`.

**Net effect on the numbers already in this doc**: `cmd_code` predictions are *unchanged* -
prepending the real discriminator byte into the payload (rather than "spending" it as a fake op)
and dropping the phantom repeated-op-byte cancel out exactly in the length formula (e.g. indicator
LED: old `7+1+6B=0x0E` vs new `7+7B=0x0E`, same value). Only `op_code` (now `0x8E` for all three)
and the payload's leading byte (now literally present as data - `0xF7` for indicator LED/motion
settings, `0xEF` for scenes) changed. Fixed in `devices.py`'s `set_indicator_led()`,
`set_motion_sensor_settings()`, and `execute_scene()`; **motion-sensor schedule write (`0x0B`,
documented further below) was NOT yet updated** - it's still only documented, not wired into a
real send, but almost certainly has the exact same bug (same `SetMotionSensorScheduleCommand`
class, same `DefaultImpls.c`→`h()` path) and needs the identical correction whenever it's built.

### Fine/fade brightness — `op_code = 0xE2`, sub-command `0x08`

**WIRED IN, EXPERIMENTAL**: `devices.py`'s `set_fine_brightness()`, `cmd_code = 0x0F` (predicted).
Exposed via HA's standard `light.turn_on(transition=...)` (no custom service needed - `ATTR_TRANSITION`
was unused anywhere in this integration before, so this can't regress any existing automation).

Extends the same command family `set_lightshow` already uses (`0xE2` outer, `0x11 0x02` prefix).

- Payload after `[0x11, 0x02, 0x08]`: `brightness × 10` as **big-endian u16** (0–1000, i.e. tenths
  of a percent) + fade duration in **milliseconds** as **big-endian u16** (max ~65.5s).
- HA's `light.turn_on(transition=...)` (seconds) maps directly: `fade_ms = round(transition * 1000)`.
- Confirmed: `SetFineBrightnessCommand.java` line 49 (`f20525r = {-30,17,2,8}`), payload builder
  `x()` lines 120-129, `writeShort` calls read directly (no decompiler ambiguity on byte layout).
- `cmd_code = 0x0F` is **predicted**, not confirmed against a live capture - via the length formula
  in "TCP relay envelope research" above.
- Adjacent, unrelated: `SetBrightnessCommand.java` (`{-46,17,2}` = `0xD2 0x11 0x02`, plain 0-100
  int, no fade) — a *different*, coarser opcode family, not needed since cync-lan's existing
  `set_brightness` already works.

### Full light-run-mode incl. MultiColor/MusicShow — `op_code = 0xE2`, sub-command `0x07`

**WIRED IN, not experimental** — no `cmd_code` risk here, it reuses `set_lightshow`'s
already-confirmed `cmd_code = 0x0E`. `devices.py`'s `set_light_effect()` + `const.py`'s
`LIGHT_RUN_MODE_EFFECTS` cover all 5 modes; exposed via the light entity's normal `effect`
attribute/`effect_list`, same as the original LightShow-only presets. The third payload byte
("randomNonce") is confirmed genuinely random and unvalidated by the receiving device
(`SetLightRunModeCommand.java:124`: `Random.nextInt()` on every real send) - a constant `0x00` is
safe for every new preset, no captured value needed.

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

**CORRECTION, this session's later work**: this turned out not to be the cheap win it looked like.
`PacketBuilder.build_control_packet()`'s routing struct packs `target_id` as a single byte
(`_require_u8`, `src/cync_lan/packet/builder.py:215`) - group synthetic addresses (32768+) cannot
be passed as `target_id` as-is, they'd fail validation outright. No code anywhere in cync-lan
sends to anything but a single-byte device address today. Whether the real wire protocol supports
a wider target field elsewhere (a different routing field, or a different packet type entirely for
group commands) is still an open question - **dropped from this round**, needs dedicated protocol
research before it's buildable, not a quick follow-up.

### Scenes control — real `op_code = 0x8E` (was wrongly `0xEF`, see CORRECTION above)

**WIRED IN, EXPERIMENTAL — the riskiest of the wired-in commands.** `devices.py`'s
`execute_scene()`, exposed via the `cync_lan.experimental_execute_scene` HA service, targeting the
"Cync LAN Bridge" device (identifiers=(DOMAIN, entry_id), no per-device target - Scenes are
home-wide) rather than an individual device. **Not yet real-hardware tested after the `0x8E`
correction** (only `set_indicator_led`, its sibling in the same command family, has been tested and
confirmed broken/now-fixed - see CORRECTION above). Two independent guesses still compound here:
`cmd_code = 0x0C` (predicted via the length formula) *and* `target_id = 0x00` (guessed as
`MeshAddress`'s documented "none/self/unassigned" sentinel - the real captured packet that
confirmed `0x8E` used a `target_id`/`sub_id` of `0xFF`/`0xFF` instead, for its own broadcast-style
command, so this specific guess is still unconfirmed either way). Flagged most prominently in its
service description for this reason.

- Confirmed: `ExecuteSceneCommand.java` line 54, `f20351p = {-17,17,2}` = `{0xEF,0x11,0x02}` - this
  array is the payload's own leading bytes, not cync-lan's outer `op_code` (see CORRECTION above).
  Payload (`x()` lines 198-207): `[sceneId(1 byte, 0-255), 0x01]`, now prefixed with `0xEF` as
  `devices.py`'s `execute_scene()` sends it: `[0xEF, 0x11, 0x02, sceneId, 0x01]` (5 bytes),
  `repeat_op_code=False`. Scenes are scoped **per-home** (`Location`), not per-group
  (`SceneModel.java` line 96: `SceneId(id, locationId)`).
- The `0x1E` byte from the original ask is real but belongs to a **separate legacy dispatch path**
  for non-mesh device types (`ExecuteSceneCommand.g()` line 179,
  `xlinkCommandDelegate.g((byte) 30, ...)`) — a distinct xlink call, not confirmed to be cync-lan's
  `cmd_code`. Treat as a separate legacy opcode, not part of the mesh-command family above.
- `cmd_code = 0x0C` is **predicted**, not confirmed against a live capture.

### Indicator LED ring — `op_code = 0x8E`, `cmd_code = 0x0E` — **CONFIRMED WORKING on real hardware**

**WIRED IN, CONFIRMED**: `devices.py`'s `set_indicator_led()` was tested against real hardware with
the original `op=0xF7` guess and came back a total no-op; re-tested after the `0x8E`
correction (see CORRECTION above) and **the user confirmed it works**. Both `op_code` (`0x8E`) and
`cmd_code` (`0x0E`) are now proven for this command, not just predicted -
`_warn_experimental_cmd_code` was removed from `set_indicator_led()` accordingly. Exposed via the
`cync_lan.experimental_set_indicator_led` HA service (service name kept as-is - renaming would
break any automation already built against it - but its description text now says "confirmed
working" instead of "predicted, not confirmed").

**UPDATE, later this session**: also exposed as 4 real HA config entities - `select.py`'s
`CyncLanIndicatorLedModeSelect`/`CyncLanIndicatorLedColorSelect`, `number.py`'s
`CyncLanIndicatorLedBrightness`, `switch.py`'s `CyncLanIndicatorLedWifiBlinkSwitch` - rather than
only the raw service, following an audit of Home Assistant's own docs on when a service call should
be a real entity instead (`assumed_state`/`EntityCategory.CONFIG` are HA's documented pattern for
exactly this "can command, can't read back" situation). Both surfaces converge on one shared
per-device cache in `bridge.py` (`IndicatorLedState`/`set_indicator_led_field`) so a service call
and an entity write can never diverge. The service stays for backward compatibility.

This confirmation is also indirect evidence *for* the sibling commands below (motion sensor
settings, scenes): it proves the `0x8E` op, the `repeat_op_code=False` envelope shape, and the
length-formula `cmd_code` prediction methodology all hold for at least one real command in this
family - but their own specific `cmd_code` values are still unconfirmed until tested individually.

Same underlying payload-prefix family as motion-sensor settings (`0xF7 0x11 0x02`), sibling
sub-command - both now correctly sent under the shared `0x8E` outer op.

- Confirmed: `SetStatusIndicatorSettingsCommand.java` (`OPCODE_BYTES = {-9,17,2,6}` - this array is
  the payload's own leading bytes, not cync-lan's outer `op_code`, see CORRECTION above),
  `StatusIndicatorSettings.java`, `LEDIndicatorMode.java`/`LEDIndicatorColor.java`. Payload builder
  `Q()`: `[(mode<<4)|color, brightness(1-100), wifi_disconnect_flag(0/1)]` after the 4-byte opcode
  array - `devices.py` now sends the full array (`0xF7,0x11,0x02,0x06`) + these 3 bytes as one
  7-byte payload under `op_code=0x8E`, `repeat_op_code=False`. `LEDIndicatorMode`: ALWAYS_ON=0,
  ALWAYS_OFF=1, NORMAL=2. `LEDIndicatorColor`: WHITE=0, RED=1, GREEN=2, BLUE=3 (a 4-value enum, not
  full RGB).
- "WiFi-disconnect toggle" is **not a separate feature** — it's byte index 3 of this same payload
  (blink-on-disconnect flag for the indicator LED), not a device behavior setting for what happens
  functionally when WiFi drops. No evidence anywhere in the decompile of an actual
  network-loss-behavior command.
- `cmd_code = 0x0E` is **predicted**, not confirmed against a live capture.
- Both this command and motion-sensor settings were traced down into the app's shared BTLE
  delegate interface (`XlinkCommandDelegate.java`, `h(byte[] payload, MeshAddress, msgId, msgId2,
  Continuation)`) — confirmed that layer carries no field resembling `cmd_code` either. Reinforces
  the "TCP relay-specific, not mesh-layer" theory above, but see the "not yet checked" note in that
  section — this was the BTLE GATT delegate specifically, not any TCP/cloud-relay equivalent. This
  same delegate interface is also what routes both commands (and `ExecuteSceneCommand`) into the
  hardcoded `0x8E` op via `XlinkDeviceManager.CommandDelegate.h()` - see CORRECTION above.
- Worth a follow-up read: `com/savantsystems/oneapp/domain/devices/model/Component.java:2845`
  (`LightRingIndicator`) — a second, UI-level mode/brightness/color enum
  (`LightRingIndicatorMode`) distinct from `LEDIndicatorMode`, translated via
  `LightRingIndicatorModeToLEDIndicatorModeMapper` — unclear if it maps 1:1 or adds states.

### Motion-sensor schedule write — payload leads with `0xF7`, sub-command `0x0B` — **NOT YET WIRED IN, needs the `0x8E` correction applied before it is**

Third sibling in the `0xF7 0x11 0x02` family (alongside motion/ambient settings at `0x07` and
indicator LED at `0x06`). Writes one of a group's 4 fixed motion-sensor schedule slots — see
"Cync-native automations (scenes, schedules, motion-sensor schedules)" below for the full data
model this command writes.

- Confirmed: `SetMotionSensorScheduleCommand.java` lines 85-193, `OPCODE_BYTES = {0xF7,0x11,0x02,0x0B}`.
  Payload after the 4-byte opcode: a flags byte (slot id 0-3 packed with mode bits and an
  RGB-vs-CCT flag), start hour, start minute, end hour, end minute, brightness, then either a CCT
  byte or 3 RGB bytes depending on the flag.
- **Same op-family bug as indicator LED/motion sensor settings/scenes applies here too** (see the
  "CORRECTION" section above) - `SetMotionSensorScheduleCommand.java:129-130` routes through the
  identical `XlinkCommandDelegate.DefaultImpls.c`→`h()`→hardcoded-`0x8E` path. The `0xF7` above is
  the payload's own leading byte, not a real outer `op_code` - if/when this gets wired into a real
  send, it must use `op_code=0x8E`, `repeat_op_code=False`, and prepend `0xF7` to the payload
  (`devices.py`'s `set_indicator_led()`/`set_motion_sensor_settings()` are the reference
  implementation for this pattern). Not corrected here only because it was never wired into a real
  send in the first place - purely documented until now.
- Blocked: `cmd_code` — apply the length formula from "TCP relay envelope research" above the same
  as any other command here (payload length is confirmed, so this one's actually computable, not
  just theoretically so) - remember the formula's payload-length input must now include the leading
  `0xF7` byte, per the correction above.
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
   already-confirmed production values.
2. ~~Wire up every "blocked: `cmd_code`" command using the formula from step 1~~ — **done**: fine/fade
   brightness, indicator LED, scenes, and motion/ambient sensor settings are all wired into real
   sends now, exposed as `cync_lan.experimental_*` HA services (`custom_components/cync_lan/
   services.py`). None of these are independently confirmed against a live capture (the formula's
   source class is `@Deprecated` in the app) - **the highest-value remaining step is real-world
   testing/reporting from users**, not further research, now that the plumbing exists.
3. **Group control needs real protocol research, not just address-targeting** - dropped from this
   round after finding `target_id` is hard-capped to a single byte in cync-lan's own packet builder
   (see the Groups section above's correction). Whether the real wire protocol has a wider target
   field elsewhere is unknown.
4. A real packet capture (MITM of a device's TCP session, which cync-lan's DNS-redirect setup
   already positions for) remains the way to fully confirm the length-field formula, resolve group
   control, and cover Scenes'/Schedules' write path (not yet analyzed - see docs/cync_automations.md).
5. **Whether the official app's cloud dependency (and, further out, new-device provisioning) could
   ever be fully replaced by a self-hosted server** - see docs/cloud_independence_research.md. The
   app's own device-control channel turns out to be just as unauthenticated as device firmware,
   BLE-provisioned device identity is confirmed client-side, and a follow-up pass (native library
   triage + a targeted re-decompile) confirmed the BLE pairing/session-key crypto is local-only too
   (mesh credentials come from an already-paired hub over BLE or are locally synthesized, no
   server-issued secret found anywhere). Remaining: a live BLE capture would still be the only way
   to get 100% certainty, but it's now a confirmatory step, not resolving an open blocker.

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
lines, not reachable from GE/Cync's `GECommissioningDataSource`).

**CORRECTION, later session**: `CommissionBuilder.java` alone only manages cloud-side
Location/Group/Subgroup placement, as noted above - but a more thorough pass found the actual
per-device mesh-address allocation this doc previously said wasn't in the app. It's local, not
device/bridge-side. See `docs/cloud_independence_research.md`'s "BLE provisioning" section for the
full writeup - short version: `BaseNonHubDeviceCommissionService.y()` (`setMeshAddressOperation`,
step 6 of a 20-step BLE commissioning pipeline) constructs each device's `MeshAddress` locally in
the app, and `DeviceId.Companion.b(macAddress)` derives `deviceID` as `"{MAC}.{index}"` - also
local, from the MAC read directly off the device over BLE. Neither requires a cloud round trip. The
single cloud call in the pipeline (`writeChangesToCloudOperation`, step 12) comes after both of
these and reads as a "sync what I already decided" write, not an identity-issuing call.
