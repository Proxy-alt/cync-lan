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


> **This file is mirrored on three branches** (`core`, `python`,
> `feature/ha-custom-component`), because each is a separately published
> artifact with its own repo view. `core` is canonical. Edit it there and
> copy to the others in the same change - CI fails the build if they drift
> (see `.github/workflows/tests.yml`'s `docs-in-sync` job).

### Source trees

Two different decompiles are cited across this repo's docs, and they are not interchangeable:

- **v1** — `cync_decompiled/`. The original jadx run. Everything in this doc dated 2026-07-16
  (background agents, cross-referenced against this repo's own `src/cync_lan/devices.py`) is
  against v1.
- **v2** — `cync_decompiled_v2/`. A re-decompile with anti-inlining flags, which recovers method
  bodies v1 had inlined away. `ble_provisioning_protocol.md` and the "Hub command family"
  section below are against v2. v2 also carries ~60 files of inline
  `[cync-lan reverse-engineering note ...]` comments recording earlier analysis.

Cite class paths relative to a tree's `sources/` root — e.g.
`sources/com/gelighting/cbygekit/services/devices/command/AddAutomationHubCommand.java` — rather
than by absolute local path, and say which tree. Neither tree is in version control, so a claim
that cannot be re-derived from a class path is a claim that will not survive losing the folder.
Line numbers in older sections refer to v1 and will not match v2; the class and field names do.

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

### The leading `0x11, 0x02` is the Telink vendor ID — **confirmed**

Every payload above opens with `0x11, 0x02`, and it is not a per-command constant to be
copied around: it is the **Telink mesh vendor ID `0x0211`, little-endian**.

Confirmed against [`juanboro/cync2mqtt`](https://github.com/juanboro/cync2mqtt)'s `acync`
(Apache-2.0), an independently working BLE implementation descended from
`google/python-dimond` and `python-tikteck`. It drives the same devices over Bluetooth
instead of TCP, and constructs its packets like this:

```python
packet[7] = command          # the op_code below
packet[8] = vendor & 0xff    # 0x11
packet[9] = (vendor >> 8)    # 0x02   -> vendor = 0x0211
packet[10:] = data           # arguments only, no 0x11 0x02 prefix
```

So the two transports carry the *same* mesh command with different framing: BLE gives the
vendor its own field, while this project's TCP path embeds it at the head of the payload.
That is why `acync`'s command bytes line up with the table above:

| `acync` (BLE) | this table (TCP) |
|---|---|
| `0xD0` + `[power]` | `set_power` `0xD0`, `[0x11,0x02,state,0,0]` |
| `0xD2` + `[brightness]` | `set_brightness` sol-lamp variant `0xD2` |
| `0xE2` + `[0x05, temp]` | `set_temperature` sol-lamp variant `0xE2` |
| `0xE2` + `[0x04, r, g, b]` | (RGB, via the same `0xE2` family) |
| `0xDC` inbound | status notifications |

Two things follow. The sol-lamp `0xD2`/`0xE2` variants are corroborated by an
implementation that demonstrably controls real hardware — independent of this project's
own decompilation, and from a different source lineage. And the opcode table is
transport-independent: anything documented here should port to a BLE transport by moving
`0x11, 0x02` out of the payload and into the vendor field, rather than by re-deriving it.

### The transport-independence claim is now **confirmed on hardware** (2026-07-28)

The paragraph above was reasoning from two implementations agreeing. It has since been
tested directly, with `research`'s `probes/ble_control_probe.py`:

- The Telink session handshake completed against a wired Cync switch, with
  `verify_pairing_response` reporting **mutual auth verified** — the device proved it
  had derived the same key material, so the credentials and the whole handshake are right.
- Inbound traffic decrypted into sensible plaintext: vendor `0x0211` sitting at bytes
  `8:9` exactly as the framing above predicts, plus readable ASCII in the payload.
- A `set_power` (`0xD0`) built by moving `0x11, 0x02` into the vendor field **changed the
  switch's state**, and `cync-lan` reported that change over its own TCP connection.

That last point is the load-bearing one. The command left over Bluetooth and the
confirmation arrived over TCP, so the two transports corroborate each other and the
result cannot be a false positive.

Brightness has since been confirmed over BLE too, and it produced a result the table
above does not predict. **Both** forms changed the brightness of the same wired dimmer:

- `0xF0` with `[0x01, bri, 0xFF, 0xFF, 0xFF, 0xFF]` — the non-sol form, which is what
  `devices.py` sends to this device class over TCP;
- `0xD2` with `[bri, 0x00, 0x00]` — the **sol-lamp** form, which by the table should not
  have applied to a wired dimmer at all.

So the sol-lamp split is real on the TCP side but is not, on this firmware, a hard
gate over BLE. That does **not** make the two equivalent, and the distinction is
deliberately kept in `ble_mesh.py`. The verification channel was cync-lan's own
reporting, which surfaces the brightness level and little else — it would not reveal a
difference in fade behaviour, in what the device persists across a power cycle, or in
sub-percent precision. `set_fine_brightness` extends the `0xE2` family precisely because
the basic form cannot express that last one, which is reason enough not to treat the two
families as interchangeable on the strength of one observation.

Scope, precisely: `0xD0` and both brightness forms are confirmed over BLE. Colour
temperature and RGB are not — they ride the same `0xF0` family whose brightness member
works, so they are better founded than a guess, but nobody has moved either.

### Notifications work — an earlier note here said they did not

That earlier claim was wrong, and it is worth saying why: it came from testing one
sequence and generalising. What fails is **BlueZ's `StartNotify`**, which answers with
GATT `Unlikely Error` — and the device does expose a `0x2902` CCCD, at handle 19, so it
is refusing a legitimate subscribe.

But the CCCD is not how this protocol turns reporting on. Writing `0x01` to
characteristic `...1911`'s **value** is. `google/python-dimond` — the origin of this
implementation lineage — does exactly that and never writes a CCCD at all; bluepy
delivers notifications regardless. With the enable-write first, **16 status packets
arrived and decrypted correctly** on a connection whose `StartNotify` had just been
rejected.

The inbound `0xDC` slot layout is `[id, presence, brightness, extra]`, two slots per
packet — and the presence rule appears **inverted** relative to acync, which skips a
slot whose second byte is zero. Across those 17 captured packets:

| `byte[1]` | slots | contents |
|---|---|---|
| `== 0` | 9 | brightness `100`, extra `255` — plausible device state |
| `!= 0` | 25 | uniformly `brightness=0, extra=0`, with `byte[1]` varying like noise |

So a zero presence byte is treated here as *data-bearing*. **Plausible, not
confirmed** — one capture, one mesh, and it contradicts an implementation known to work
elsewhere. The `byte[1] != 0` records are unexplained and may be a different record type
sharing the `0xDC` opcode.

**The mesh credentials do not come from the hub.** They are in the cloud export that
`cloud_api.py:_parse_raw_export` already writes: the home's `mac` is the Telink mesh
name and its `access_key` is the mesh password, both confirmed by the run above. Worth
stating plainly because the `query_mesh_credentials` button suggests otherwise, and that
button is a hub command — a family that currently gets no reply at all (see
`hub_envelope_ab_test.md`). Nothing about BLE control depends on it, or on DNS
redirection.

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

**Operational prerequisite, RESOLVED**: the real Cync app requires physically waking the sensor
first (hold the off button ~5s until the LED turns green) before it accepts settings edits -
confirmed this gate is just the device's ordinary online/offline mesh status (the same signal
`bridge.is_online(dev_id)` already tracks), not a BLE/GATT discoverability scan, and no
programmatic wake command exists. See "Operational prerequisite: motion sensors must be woken
before settings/schedule writes" further below for the full research trail.

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
`set_motion_sensor_settings()`, and `execute_scene()`. **UPDATE**: motion-sensor schedule write
(`0x0B`, documented further below) has since been wired in too - `set_motion_sensor_schedule()`
has the identical correction applied (confirmed via the same `SetMotionSensorScheduleCommand`
class, same `DefaultImpls.c`→`h()` path), same as its siblings above.

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

**UPDATE, follow-up pass on "Reveal" specifically** - re-confirmed `set_light_effect("reveal")`
(modeCode `0x03`) is the real app's dedicated Reveal toggle button
(`RevealFragment.i0()`→`LightControlViewModel.p()`→`Command.SetLightRunMode`), already fully wired
in `devices.py`, nothing further needed for it. **A second, separate, real code path exists that
is NOT wired in** and is redundant with the above: selecting Reveal via the app's color-tab picker
sends it through the everyday `SetComboCommand` (op `0xF0`) instead, with a 2-byte (not 4-byte)
color-type sentinel `[0xFF, 0xF0]` in place of the usual CCT `[pct,0,0,0]`/RGB `[0xFE,r,g,b]` shape
(`SetComboCommand.java:816-818`; the singleton `RevealColor` object carries no Full-Color-vs-
Soft-White distinction - that's purely the bulb's own SKU/firmware, not a wire field, per
`ProductModel.java:137-171`'s per-SKU Reveal entries). `SetComboCommand` explicitly rejects
`RevealColor` for hub-relayed devices (`"RevealColor is not supported by hubs"`,
`SetComboCommand.java:339,421,532,614,705`) - only works over direct XLink mesh. Since this path
produces the same end-user effect as the already-wired `set_light_effect("reveal")`, it's
documented here for completeness but not worth implementing as a second way to do the same thing.

**Custom MultiColor scheme creation** (uploading arbitrary per-segment RGB data, not just
activating a saved one) is a **separate, unrelated opcode**: `SetMultiColorSegmentsCommand.java`,
`{0xF7, 0x11, 0x02, 0x4E}` — payload is a gradient-mode toggle, segment count, or up to 2 segments
per packet as `[position_or_255, R, G, B]`.

**SHIPPED, EXPERIMENTAL**: `devices.py`'s `CyncDevice.set_multicolor_gradient_mode()`/
`set_multicolor_segment_count()`/`set_multicolor_segments()` implement these 3 confirmed wire
primitives exactly (dispatched via the same non-hub `0x8E`-relay-bug family as add_to_scene/
set_indicator_led/etc, per `SetMultiColorSegmentsCommand.mo14023N()`'s
`XlinkCommandDelegate.DefaultImpls.c()` call - no product-family branch exists for this command at
all, unlike add_to_scene/set_group_membership/remove_from_scene). Per-segment position and color
are confirmed INDEPENDENTLY nullable (`MultiColorSegmentData.java`'s own two separately-`@Nullable`
fields) - position defaults to the `0xFF` sentinel, color defaults to `0,0,0`, not tied together.
Position's `1-120` range is a literal confirmed directly in `SegmentData`'s own bounds check, not
inferred. What's **not** wired in, and not just a matter of exposing another primitive:
this project does not orchestrate the multi-send SEQUENCE a real custom scheme needs (what order
the app sends gradient-mode/count/segment-data in across possibly many chunked sends for >2
segments, and any timing between them, isn't traced from the decompiled source) - callers
must chunk/sequence themselves.

Also `SetMultiColorSchemeDirectCommand.java` (xlink `0x89`, "entertainment"/live-streaming variant)
and `SetMultiColorBitmapCommand.java` (tile/matrix bitmap data for other product types) remain
**out of scope, not just untested** - both dispatch via methods (`m14393b`/`m14027t`) never traced
anywhere else in this research, structurally distinct from every other confirmed command family in
this file, and "entertainment"/live-streaming framing strongly implies a continuous low-latency
protocol rather than a one-shot command - a materially bigger feature than exposing another
primitive, unlike the 3 `SetMultiColorSegmentsCommand` primitives above.

Related for a future scene-export feature: `AddDeviceSceneCommand.java` (`{-18,17,2}` =
`0xEE 0x11 0x02`) documents the 6-byte per-device state block scenes store
(`mode, brightness/param, color-temp-or-254-for-RGB, R, G, B`).

**UPDATE, follow-up pass - full payload including the Schedule "fade" feature resolved.**
`AddDeviceSceneCommand` is what actually programs a device's on-device scene-slot state -
called once when a scene (or the implicit per-device scene backing a simple Schedule) is
created/edited, not at trigger time. Two payload shapes depending on device routing:

- **Non-hub-routed devices** (`AddDeviceSceneCommand.java:186-192,279-284`): `[0xEE,0x11,0x02] +
  [actionTypeByte, sceneNum] + [mode, param, colorType, R, G, B] (the 6-byte block above) +
  [fadeByte, 0xFF]` - 13 bytes. **This is the same `0x8E`-relay bug class as indicator LED/motion
  settings**: dispatched via `XlinkCommandDelegate.DefaultImpls.c` → `h()` →
  `XlinkDeviceManager.CommandDelegate.h()` (`XlinkDeviceManager.java:1050-1053`), which hardcodes
  outer op `(byte)-114` = `0x8E` - the embedded `0xEE` is not the real outer op_code, exactly like
  the already-confirmed siblings. **Shipped**: `devices.py`'s `CyncDevice.add_to_scene(scene_id,
  cct=, rgb=, fade=, sub_id=)` implements this exact path with the `0x8E` fix applied; the
  Hub-routed shape below is not implemented (logs an error instead of guessing at an unconfirmed
  format).
- **Hub-routed devices** (`AddDeviceSceneCommand.java:193-251`): a manually-built `WriteBuffer`
  frame with `FrameCode` headers + an explicit little-endian `MeshAddress` target, dispatched via
  the raw pre-framed `e()` method (same dispatch class as the pure Hub Scenes/Schedules commands
  elsewhere in this doc) - a structurally different payload from the non-hub path above, not just
  a different op_code.

**`RemoveDeviceSceneCommand`** (the counterpart - removing a device's captured state from a scene
without deleting the scene) is simpler and, unlike `AddDeviceSceneCommand`, has **both** branches
confirmed and shipped: opcode array `{0xEE,0x11,0x02,0x00}` (fixed trailing `0x00` - no actionType/
mode/color/fade fields, since there's nothing to configure) + sceneId (1 byte) = 5 bytes.
Non-hub-routed devices hit the same `0x8E`-relay substitution as `AddDeviceSceneCommand`'s non-hub
path. Hub-routed devices (`is_sol_lamp`) turned out to route through the same trustworthy
`mo14054f()` envelope already confirmed for `set_group_membership()`'s `is_sol_lamp` branch - a
real op_code `0xEE`, not the manually-built `WriteBuffer`/`FrameCode` frame that blocks
`AddDeviceSceneCommand`'s hub path. **Shipped**: `devices.py`'s
`CyncDevice.remove_from_scene(scene_id, sub_id=)` implements both branches.

**The fade byte** (`ScheduleFade.java:25-32`, 1-byte signed enum): `NO_FADE=-1(0xFF)`,
`FADE_10_SECONDS=1`, `FADE_30_SECONDS=2`, `FADE_1_MINUTE=3`, `FADE_5_MINUTES=4`,
`FADE_10_MINUTES=5`, `FADE_20_MINUTES=6`, `FADE_30_MINUTES=7` - a coded duration bucket, not raw
seconds. It's a field on `SceneModel` (`SceneModel.java:47-48`), not on the Schedule command
itself - `CreateScheduleHubCommand`'s own payload has no fade byte at all
(`CreateScheduleHubCommand.java:59-68`); every Schedule (even a single-device one) is internally
an implicit `SceneModel` under the hood (`ScheduleServiceDefault$createDevicesSchedule*` classes),
so this mechanism does cover schedules despite living on the Scene side. **Confirmed hardware-side,
not a software ramp**: `ExecuteSceneCommand` (what a schedule trigger actually fires) never resends
color or fade data for real scenes (`ExecuteSceneCommand.java:137-144,198-207`) - it just
references the `SceneId`, meaning the bulb's own firmware executes the fade autonomously using the
byte it received once at scene-programming time. Not general-purpose: `SetComboCommand.y()` (the
everyday brightness/color-set command) has no fade field anywhere in its payload - fade only
exists via the Scene/Schedule-programming path above.

**Shipped** as `add_to_scene()`'s `fade` parameter (default `0xFF`/`NO_FADE`) - not blocked by the
Hub-commands transport question (`docs/cync_automations.md`) since this non-hub-routed path is
confirmed to ride the same `mo14054f`/`mo14056h` methods already proven to carry real TCP-relay
traffic.

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

**CORRECTION (twice), this session's later work.**

First pass looked like a dead end: `PacketBuilder.build_control_packet()`'s routing struct packs
`target_id` as a single byte (`_require_u8`, `src/cync_lan/packet/builder.py:215`) - group synthetic
addresses (32768+) can't be passed as `target_id` as-is, they'd fail validation outright. Dropped
from that round pending dedicated protocol research.

**Second pass, resolved**: `target_id` and `sub_id` were never two independent fields.
`XlinkDeviceManager.CommandDelegate.mo14054f()` (the real app's outer-envelope builder, confirmed
byte-for-byte, re-traced with fresh eyes after the earlier pass mischaracterized it as a possibly
separate/legacy code path - it isn't; `SetComboCommand`, the group-membership `0xD7` command above,
and the `0x8E`-family commands all funnel through this exact same method) writes a genuine 2-byte
little-endian `MeshAddress` destination field. Lining up byte positions: cync-lan's own
`routing = [msg_id][0000][target_id][sub_id]` is structurally identical to the app's
`[msgId_lo][msgId_mid][msgId_hi][00][00][dest_lo][dest_hi]` whenever `msg_id < 256` (mid/hi bytes
naturally zero) - meaning **`target_id` (low byte) and `sub_id` (high byte) already jointly
represent one 16-bit MeshAddress**, they just never needed to be anything but `sub_id=0` for
ordinary single-gang devices, so nobody noticed the high byte was live. Group addresses (32768-65535,
exactly cync-lan's own `group_id` dict key - see `cloud_api.py`'s `raw_group["groupID"]`) fit
entirely inside that same 16-bit space. **No `PacketBuilder` changes are needed at all** -
`_require_u8` already accepts any byte 0-255 in both fields; a caller just needs to stop hardcoding
`sub_id=0x00` and instead split a group address as `target_id = addr & 0xFF, sub_id = (addr >> 8) &
0xFF`.

**WIRED IN, EXPERIMENTAL, but unlike every other experimental command in this doc, not a `cmd_code`
guess**: `devices.py`'s `set_group_power(group_id, state)` reuses `set_power`'s exactly-confirmed
`op=0xD0`/`cmd_=0x0D`/payload byte-for-byte - the only unconfirmed thing is the addressing itself.
Two real gaps remain, honestly: (1) the one real `0x8E`-family packet capture available used
broadcast `target_id=sub_id=0xFF` (i.e. address `0xFFFF`), which is byte-identical whether you think
of it as "two independent 0xFF bytes" or "one 16-bit `0xFFFF` address" - it doesn't independently
prove the 2-byte model, the byte-layout argument above is the real evidence, not this capture; (2)
whether the app itself ever relies on a true one-packet group broadcast is still unclear -
`DeviceServiceDefault.mo13857w` ("sendMulticastCommand") takes a `Collection<DeviceId>` (individual
device IDs) and its `MulticastStrategy` enum (`CONTINUE_ON_FAILURE`/`BREAK_ON_FAILURE`) reads like
per-item loop error handling, not an addressing mode - circumstantial evidence the app fans out
per-device commands for "group" actions rather than ever sending one true group-targeted packet.
So: the wire format can *represent* a group address, cync-lan can now send one with zero new
plumbing, but whether real device firmware actually *honors* it as "respond as the whole group" has
never been tested - that's what `cync_lan.experimental_set_group_power` (`custom_components/
cync_lan/services.py`) exists to find out.

**Group MEMBERSHIP itself, WIRED IN, EXPERIMENTAL** — the actual `0xD7` `ControlDeviceGroupCommand`
described at the top of this section (add/remove a device to/from a group's pub/sub address, not
"control the group's state") is now implemented: `devices.py`'s `CyncDevice.set_group_membership
(group_id, member, reach_flag=0x00)`, exposed as `cync_lan.experimental_set_group_membership`.
Unlike every other command in this doc it targets an individual device's own address - the group
address is payload data, telling one specific device "start/stop listening on this group's
address," not a broadcast. This is genuinely new functionality cync-lan didn't have before
(creating/managing group membership, not just controlling an existing group's power) - a real step
toward managing structure, not just runtime state, from HA.

**UPDATE, corrected after follow-up research - the command is genuinely dual-path, not always
real-0xD7.** `ControlDeviceGroupCommand.mo14013g()` branches on
`xlinkCommandDelegate.getDeviceType().getProductType().f31219d` (`ControlDeviceGroupCommand.java:192`).
Traced this flag to its source: it means "is this device's `ProductType` `Sol` or `C-Reach`" -
the SDK's own internal "is this a Hub product" flag (`ProductType.java:195-199`;
`XlinkHubDeviceController`'s constructor literally asserts `if (!productType.f31219d) throw
UnsupportedDeviceTypeException(... "is not a hub")`) - **not** a generic hub-relay/BLE-vs-WiFi
distinction as originally assumed when this command was first wired in. cync-lan already tracks
the exact same distinction via `is_sol_lamp` (`metadata.opcodes.sol_lamp`, true only for device
type 80 in cync-lan's current catalog - see `is_sol_lamp`'s own docstring for the sibling
`set_brightness`/`set_temperature` special-casing this same product family already uses). The
initial implementation always took the confirmed-real-`0xD7` branch - correct only for that rare
Sol/C-Reach family. For every other real device (`is_sol_lamp=False`, virtually all real Cync
hardware), the SDK instead routes through the same `0x8E` "mesh-relay" substitution bug as
`set_indicator_led`/`set_motion_sensor_settings`/etc. - `0xD7` moves from "outer op" to "payload's
leading discriminator byte." **Now fixed**: `set_group_membership()` branches on `self.is_sol_lamp`
and sends the correct shape for either case - `op=0xD7` directly (unconfirmed `cmd_code = 8 +
6-byte payload = 0x0E`) for the rare Sol/C-Reach family, `op=0x8E` with `0xD7` prepended into a
7-byte payload (unconfirmed `cmd_code = 7 + 7 = 0x0E`, `repeat_op_code=False`) for everything else.
Neither branch's `cmd_code` has been captured against a live packet.

### Scenes control — real `op_code = 0x8E` (was wrongly `0xEF`, see CORRECTION above)

**WIRED IN, EXPERIMENTAL — the riskiest of the wired-in commands.** `devices.py`'s
`execute_scene()`, exposed via the `cync_lan.experimental_execute_scene` HA service AND (newer)
`scene.py`'s `CyncLanScene` entities (one per Cync Scene, sourced from `parse_scenes()` - see
`cync_automations.md`'s "Recommendation" item 4), targeting the "Cync LAN Bridge" device
(identifiers=(DOMAIN, entry_id), no per-device target - Scenes are home-wide) rather than an
individual device. **Not yet real-hardware tested after the `0x8E`
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

### Motion-sensor schedule write — payload leads with `0xF7`, sub-command `0x0B` — **WIRED IN, EXPERIMENTAL**

Third sibling in the `0xF7 0x11 0x02` family (alongside motion/ambient settings at `0x07` and
indicator LED at `0x06`). Writes one of a group's 4 fixed motion-sensor schedule slots — see
"Cync-native automations (scenes, schedules, motion-sensor schedules)" below for the full data
model this command writes.

- Confirmed: `SetMotionSensorScheduleCommand.java` lines 85-193, `OPCODE_BYTES = {0xF7,0x11,0x02,0x0B}`.
  Real outer op is the same hardcoded `0x8E` already confirmed working on real hardware for
  indicator LED - `SetMotionSensorScheduleCommand.java:129-130` routes through the identical
  `XlinkCommandDelegate.DefaultImpls.c`→`h()` path. `devices.py`'s `set_motion_sensor_schedule()`
  implements this with `op_code=0x8E`, `repeat_op_code=False`, `0xF7` prepended to the payload -
  same pattern as `set_indicator_led()`/`set_motion_sensor_settings()`.
- **Full payload, resolved to the exact bit level** (`m14101x()`, lines 163-202): 4-byte
  discriminator array (`0xF7,0x11,0x02,0x0B`) + a flags byte + start_hour + start_minute + end_hour
  + end_minute + brightness + 3 color bytes = 13 bytes total.
  - Flags byte: `slot_id (bits 0-1, 0-3) | mode_bit | rgb_flag`. Mode bits (from the app's own
    if/else-if chain, NOT derived from `MotionSensorResponseMode.java`'s ordinals):
    DISABLED→`0x80`, OCCUPANCY→`0x00` (no bit, the implicit else case), VACANCY→`0x20`,
    SIMPLE→`0x10`. `rgb_flag = 0x40` when using RGB color (independent bit, combines with any
    mode). Bits 2-3 are never touched by any code path - confirmed reserved/always-0, not a gap.
  - `start_hour`/`end_hour`: 0-23. `start_minute`/`end_minute`: 0-59. Plain `LocalTime`
    hour/minute, big-endian order.
  - `brightness`: 0-100, range-checked in the app itself (`SensorSchedule2.java:182-195`).
  - Color tail (always 3 bytes): CCT → `[pct, 0x00, 0x00]` (0=warmest, 100=coolest, matches the
    cloud-JSON `cct` field 1:1); RGB → `[r, g, b]`, each 0-255. `RevealColor` throws
    `UnsupportedOperationException` in the app - not encodable, not offered by `devices.py`.
  - Slot id mapping confirmed identical to the already-decoded cloud-JSON model
    (`MotionSensorMappersKt.m13647a`): Morning=0, Daytime=1, Evening=2, Sleep=3.
- `cmd_code = 0x14` is **predicted** via the length formula (`7 + 13 = 20 = 0x14`) - reproduces
  exactly for both confirmed 0x8E-family siblings (indicator LED `7+7=0x0E`, motion settings
  `7+12=0x13`) but is not itself independently confirmed against a live capture.
- **Targeted at an individual device, not a group MeshAddress** - confirmed via
  `MotionSensorServiceDefault.java`'s `writeSchedule()` (~lines 894-1010): the real app resolves a
  group's member devices and fans this command out per-device, rather than ever sending it once to
  a synthesized group address. `devices.py`'s `set_motion_sensor_schedule()` is accordingly a
  `CyncDevice` instance method (targets `self.id`), not a module-level group-targeted function like
  `set_group_power()` - a genuinely different targeting model from the group-power finding above,
  confirmed independently rather than assumed to match.
- Exposed as `cync_lan.experimental_set_motion_sensor_schedule` (`custom_components/cync_lan/
  services.py`) - **never itself tested against real hardware**, unlike indicator LED. The outer
  op/payload-shape confidence transfers from the sibling commands' confirmation, but this specific
  command's own behavior is unverified.
- **Operational prerequisite, RESOLVED**: same physical-wake requirement as motion sensor settings
  above (hold the off button ~5s until the LED turns green = the device's ordinary mesh online
  status, same signal as `bridge.is_online(dev_id)`) - see "Operational prerequisite: motion
  sensors must be woken before settings/schedule writes" further below for the full research trail.
- **This is a write-side finding that doesn't need a cloud API at all** — it's a local mesh command,
  architecturally identical to every other opcode cync-lan already speaks. See the automations doc
  for why that matters for a HA-automation-to-Cync-device sync feature.

### Operational prerequisite: motion sensors must be woken before settings/schedule writes — RESOLVED against decompiled source

Originally user-reported from real Cync app usage, now independently confirmed via a full
decompiled-source research pass: to edit a motion sensor's settings in the real Cync app, the
physical device must first be woken up by pressing and holding its off button for 5 seconds until
the indicator LED turns solid green. Exact user-facing copy, `strings.xml:2354`:

> Press and hold the off button for 5 seconds to wake the device & make it discoverable. The LED
> light will turn green. Make sure your phone is within 40 feet of the device during this
> process.

Shown by `DeviceSettingsWakeUpFragment.B0()` (`DeviceSettingsWakeUpFragment.java:158-180`), shared
verbatim by 3 battery/sleep device classes (`DeviceClassification.java`: WirelessSwitch=15,
Remote=17, MotionSensor=18) - only the Lottie animation differs per device type. **Don't confuse
with commissioning's separate "setup mode" copy** (`strings.xml:1271-1275`, blinking **blue**,
shown only during first-time device add) - this is a different flow with different LED color.

**"Discoverable" is not a BLE/GATT state at all — it's the device's ordinary mesh online/offline
status**, the same `AvailabilityState` StateFlow every device type (lights, switches, sensors)
already reports. Confirmed via `MotionSensorServiceDefault.isOnline()`
(`MotionSensorServiceDefault.java:2607-2637`) → `DeviceManager.i(): StateFlow<AvailabilityState>`
(`DeviceManager.java:60`) → `AvailabilityState.Online` (`AvailabilityState.java:66-89`, a plain
sealed-class member alongside `Offline`/`Establishing`/`NoLink`/`None` - no special "discoverable"
variant exists). The wake-up screen's own gating logic
(`DeviceSettingsWakeUpFragment.java:179`) just reactively watches this same status flow flip to
connected - it runs no dedicated discovery/BLE-scan routine of its own. **This means cync-lan
already has the exact equivalent signal**: `bridge.is_online(dev_id)` (driven by the real
online/offline status packets `CyncLanEntity.available` already reads) is the same underlying
concept the real app checks before allowing an edit.

**No programmatic wake command exists** - exhaustively searched `com.gelighting.cbygekit` and
`com.savantsystems.oneapp` (the entire GE/Cync source) for "wake"/"discoverable"/"pairing
mode"/"config mode"; the only hits are in an unrelated bundled Tuya SDK and generic
Bluetooth-mesh-provisioning classes, neither wired to Cync/GE motion sensor code.
`MotionSensorService`'s full interface (`MotionSensorService.java`) has no such method. **A
previous lead in this doc, `BleDeviceCommissionService.checkMotionSensorOperation`, was
investigated and is a false lead** - it fires during initial multi-device commissioning to
re-home a sensor's "smart operation" group mapping, unrelated to wake/discoverability; not worth
re-investigating.

**A real, load-bearing finding for cync-lan's own behavior**: the real app's
`MotionSensorServiceDefault.D()`/`.B()` (writeSettings/writeSchedule) check `isOnline()` per
target device and **silently return `Ok(Unit)` — fake success — without ever transmitting the
command if the device is offline**, rather than surfacing an error. If cync-lan wants to match
real-ecosystem behavior (and give better UX than the real app does), an HA-side flow should check
`bridge.is_online(dev_id)` *before* calling `set_motion_sensor_settings()`/
`set_motion_sensor_schedule()` and tell the user to wake the device, rather than sending blind and
getting the same silent no-op the real app itself produces when the target is asleep - see
[[project_sensor_wake_prerequisite]] (agent memory) and `feedback_opcode_debugging.md`'s general
"silent mesh-command failures" note, which this confirms applies architecturally, not just as an
unverified byte-guess risk.

Full research trail: `/private/tmp/claude-501/.../scratchpad/jadx_out/BleDeviceCommissionService_fallback.java`
and `MotionSensorServiceDefault_fallback.java` (raw `jadx -m fallback --single-class` dumps of two
methods JADX's normal pass couldn't render) - not committed to this repo, regenerate from
`/Users/proxy-alt/Downloads/cync_apk`'s `classes*.dex` if needed again.

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

## Hub command family — 8 further confirmed op_codes, extracted but not wired in

Found by sweeping every class in `com/gelighting/cbygekit/services/devices/command/` for a real
dispatch byte rather than for the opcode arrays those classes also carry. None of these eight
appear anywhere in this repo's docs or `src/` today.

**These op_codes are real, and for a specific reason.** All eight dispatch through
`XlinkTranslatorKt.m14449a(seq, (byte) op, writeBuffer)` — the same call shape as
`add_automation`'s already-confirmed `0x95` (see `devices.py`'s `add_automation` docstring:
"real op_code (byte)-107 = 0x95"). On this hub/xlink path the byte argument **is** the outer
op_code. That is *not* the same as the `0x8E`-family trap in the CORRECTION section above, where
a command class's own `{0xEF,0x11,0x02}` array is the leading bytes of a payload and the real
outer op is hardcoded elsewhere. Every entry below was read from the dispatch call, not from an
opcode array.

> **Correction (2026-08-01 pass).** Reading the dispatch call was necessary but
> not sufficient: it does not show *whether the app reaches that call* for a
> given device. Two gating conditions were missed on the first sweep, and
> between them they explain why none of these ever returned a reply on real
> hardware. See "Why the hub family never answers" below the table.

`WriteBuffer` widths, from `services/devices/xlink/legacy/WriteBuffer.java`:
`m14441a()` = 1 byte, `m14442b()` = raw byte array, `m14443c()` = **4-byte LE**,
`m14444d()` = **2-byte LE**. Buffers are allocated zero-filled and the whole array is sent, so an
under-filled buffer goes out with trailing zero padding — the allocation size is the wire size.

| op_code | Command class | Request payload | Response |
| --- | --- | --- | --- |
| `0x32` | `DeleteGroupHubCommand` | 2 B — `groupAddress` (u16 LE) | none |
| `0x46` | `QueryDeviceTimeCommand` | 64 B, all zero — **Sol/C-Reach only**, see below | `DeviceTimeNotification` |
| `0x49` | `QueryHubFirmwareUpdatesCommand` | 32 B, all zero | `HubFirmwareUpdatesNotification` |
| `0x4B` | `QueryHubInfoCommand` | 64 B, all zero | `HubInfoNotification` |
| `0x4F` | `StartHubFirmwareUpdatesCommand` | 32 B — `0x00` + `{0x00,0x00}` or `{0xFF,0xFF}` | `HubFirmwareUpdateStatusNotification` |
| `0x8A` | `QueryHubMeshNameAndPasswordCommand` | 64 B, all zero | `HubMeshNameAndPasswordNotification` |
| `0x97` | `DeleteAutomationHubCommand` | 6 B — `scheduleId` (u16 LE) + 4 zero bytes | none |
| `0xAD` | `QuerySolConfigCommand` | 64 B, all zero | `SolConfigNotification` |

Confidence: **confirmed** for op_code and request payload (read from decompiled dispatch +
`WriteBuffer` calls). **Not** hardware-tested — none of these is wired into `devices.py`, and the
`cmd_code` for each would still need the length formula from "TCP relay envelope research" above.

### Why the hub family never answers

Two conditions gate these commands in the app. Neither is visible from the
dispatch call itself, and cync-lan satisfies neither. Together they account for
the A/B test result in `hub_envelope_ab_test.md` ("neither envelope produces a
reply") without the envelope being wrong at all — the frames cync-lan emits
match `Xlink.m14391a()` byte for byte.

**1. `0x46` is not the command your devices use.** `QueryDeviceTimeCommand` is
the only one of the eight whose `mo14023N()` (the Xlink path) *branches*, and
the hub-envelope dispatch sits in the far arm:

```java
if (!xlinkCommandDelegate.getDeviceType().getProductType().f31219d) {
    // every ordinary device lands here
    return XlinkCommandDelegate.DefaultImpls.m14394c(this, TELINK_OPCODE_BYTES, meshAddress, 0, cont, 12);
}
// only Sol / C-Reach reach the 0x46 hub envelope
WriteBuffer writeBuffer = new WriteBuffer(64);
... XlinkTranslatorKt.m14449a(seq, (byte) 70, writeBuffer) ...
```

`f31219d` is the SDK's "is this a Hub product" flag, already documented above as
meaning `ProductType.Sol` or `ProductType.CReach`. `ProductType.java`'s
initialiser sets it `true` for exactly those two and `false` for Light,
IndoorLightStrip, OutdoorLightStrip, NeonLightStrip, OutdoorNeonLightStrip,
CafeStringLights, Downlight, UndercabinetFixtures, LightTile, Plug, Switch,
FanSpeedSwitch, WireFreeSwitch, WireFreeSensor, WireFreeRemote, Thermostat and
Camera.

So on any normal fleet — switches, plugs, bulbs — the app never sends `0x46`.
It sends `TELINK_OPCODE_BYTES = E8 11 02 10` through `DefaultImpls.m14394c()`,
which forwards to `mo14056h()` and therefore goes out as the **`0x8E`
mesh-relay family** documented in the CORRECTION section above — a path
cync-lan already implements correctly for other commands. `0x46` is not a
"predicted cmd_code" problem; it is the wrong command for the hardware.

**2. Six of the eight cannot be broadcast.** `DeviceCommand.m()` defaults to
`false` and is overridden to `true` by `DeleteGroupHubCommand` (`0x32`),
`QueryHubFirmwareUpdatesCommand` (`0x49`), `QueryHubInfoCommand` (`0x4B`),
`StartHubFirmwareUpdatesCommand` (`0x4F`),
`QueryHubMeshNameAndPasswordCommand` (`0x8A`) and
`DeleteAutomationHubCommand` (`0x97`). Only `QueryDeviceTimeCommand` and
`QuerySolConfigCommand` leave it `false`.

`DeviceServiceDefault.sendBroadcastCommand()` treats that flag as a hard error:

```java
if (!(!deviceCommand.getF34564v())) {
    throw new IllegalArgumentException("Command " + name + " doesn't support non-self destination");
}
```

and the multicast path, rather than broadcasting, builds a
`DeviceCommand.DuplicateFactory` and fans out one unicast per `DeviceId`,
merging replies through `MulticastResultMerger`. These are self-addressed
commands: they must carry a specific device's `MeshAddress`.

cync-lan's `_query_hub()` calls `broadcast_control_command(op, cmd_, 0x00,
0x00, ...)` — `target_id` and `sub_id` both zero, i.e. the broadcast address.
That is precisely the addressing the app refuses to emit for these six.

`0x4B` and `0x8A` additionally declare `ConnectionType` set
`DeviceCommand.f34462l` = `{WIFI}` alone, where `QueryDeviceTimeCommand` uses
`f34461k` = `{BLE, BLE_PROXY, WIFI, WIFI_PROXY}`. The TCP relay is the WIFI
path, so this one is satisfied already — noted only so a future sweep does not
re-flag it.

Confidence: **confirmed via decompiled source** for both conditions.

### The `0x8E` route for `query_device_time`, as implemented

Wired into `devices.py` as `_query_device_time_telink()`. `query_device_time()`
now takes this route by default and only sends the `0x46` hub envelope when
called with `hub_product=True`, which is correct for a Sol lamp or a C-Reach
hub and wrong for everything else.

Request, matching `TELINK_OPCODE_BYTES` exactly:

```
op        = 0x8E          # mesh-relay family, repeat_op_code=False
cmd_      = 0x0B          # 7 + len(payload), as for every other 0x8E command
payload   = E8 11 02 10   # opcode 0xE8, Telink vendor id 0x1102, one parameter
target_id = 0x00          # broadcast is legitimate here - m() is false
```

The reply is **not** an Xlink/HDLC frame, so it does not arrive through
`try_resolve_xlink_notification()`. It comes back as a relayed Telink mesh
notification, and `services/devices/telink/NotificationType.java` maps it:

```java
QUERY_TIME((byte) -23, DeviceTimeNotification.TelinkParser.f36440a)
```

`(byte) -23` = **`0xE9`** — request `0xE8`, response `0xE9`.

Frame layout is Telink's standard `sno[3] src[2] dst[2] op vendor[2] par[10]`,
which is why every parser in that registry reads from offset 10: offset 10 is
`par[0]`. `try_resolve_telink_notification()` locates the frame by searching
for the `11 02` vendor id and reading the opcode from the byte before it,
rather than trusting a fixed offset into whatever wrapper the relay used. A
real captured `0x83` supports that choice — its inner data ran
`1e 00 00 00 fa db 13 00 15 35 11 b7 00 b7 00 db 11 02 01 00 ...`, with the
Telink frame starting 8 bytes in.

`DeviceTimeNotification.TelinkParser` reads `par` as: year (u16 LE at
`par[0..1]`), month, day, hour, minute, second, then a DST marker and a packed
UTC offset. That last pair is the layout's only difference from the Xlink one
and is deliberately not applied — it is the device's configured offset, not a
timezone, and applying it would reinterpret the wall-clock reading the caller
asked for.

Confidence: **confirmed via decompiled source**; not yet hardware-tested.

### Implementation status

`0x32`, `0x46`, `0x4B`, `0x8A`, `0x97` and `0xAD` are wired into `devices.py`
as of cync-lan 0.3.0. `query_device_time`'s `0x8E`/`0xE8` route was added
later, once the `0x46` gating above was understood.

The five self-addressed commands (`0x32`, `0x4B`, `0x8A`, `0x97`, plus `0x49`
below) are still emitted to the broadcast address and so are still expected
not to answer. Fixing them means threading a real `MeshAddress` through
`_query_hub()`, which is a larger change than the `query_device_time` reroute
and has not been made.

`0x49` `QueryHubFirmwareUpdates` is **deliberately not implemented**. Its
reply is not a fixed record like the others: `HubFirmwareUpdatesNotification`
reads a status byte and three 2-byte counters, then a variable-length list of
per-device entries each carrying a 2-byte id and two 10-byte version strings.
Getting the field order or the record stride wrong yields plausible-looking
garbage rather than an obvious failure, and there is no capture to check a
decoder against - so it stays documented rather than guessed at.

The three firmware-*applying* commands (`0x4F` `StartHubFirmwareUpdates`,
`StartWifiOtaUpdate`, `SetWifiOtaUpdateMode`) are **intentionally absent from
the codebase entirely**, not merely unwired. Everywhere else in this family a
wrong predicted `cmd_code` means the device ignores the packet; these flash
firmware, where the same mistake has a much worse floor. They stay documented
until someone confirms the envelope against a real packet capture.

### The two write commands

`0x97` `DeleteAutomationHubCommand` closes an obvious asymmetry: this repo already implements
`create_schedule` (`0x92`), `toggle_automation` (`0x93`) and `delete_schedule` (`0x94`), but has
no way to remove the *automation* (the trigger binding created by `add_automation`/`0x95`). The
payload is a 6-byte buffer with only `m14444d(scheduleId)` written, so bytes 2-5 are zero — the
same 6-byte frame shape as its siblings, not a 2-byte command.

`0x32` `DeleteGroupHubCommand` takes a group `MeshAddress` as `UShort`, matching
`MeshAddress.java`'s `GROUP_ADDRESS_RANGE = 32768 until 65535` already documented in
"Groups control" above. It is the delete counterpart to the `0xD7` membership command.

### Response layouts, decoded

Read from each notification's `XlinkParser.mo14294a()`. All parse little-endian.

**`0x8A` → `HubMeshNameAndPasswordNotification`** — two fixed 48-byte fields, 96 bytes total:

```
[0..47]   mesh name      (48 B, ASCII, NUL-padded)
[48..95]  mesh password  (48 B, ASCII, NUL-padded)
```

This is the most directly useful of the eight. `src/cync_lan/ble_provision.py` currently requires
`FACTORY_MESH_NAME`/`FACTORY_MESH_PASSWORD` because it can only provision a factory-default
device; `key_encrypt()` and `generate_sk()` both derive from exactly these two values. A hub that
answers `0x8A` would hand over an *already-provisioned* mesh's credentials over the LAN.

**`0x4B` → `HubInfoNotification`** — four fixed 16-byte ASCII fields, 64 bytes total:

```
[0..15]   firmware version, major part
[16..31]  firmware version, minor part   -> joined as "{major}.{minor}"
[32..47]  MAC address     (parsed via MacAddress.Companion)
[48..63]  setup code
```

**`0x46` → `DeviceTimeNotification`** (Xlink parser — 7 bytes read):

```
[0..1]  year   (u16 LE)
[2]     month
[3]     day
[4]     hour
[5]     minute
[6]     second
```

Note the app also has a *Telink* parser for the same notification reading a different layout
(offsets 10-19, adding a DST flag at byte 17 — `0xA1` true / `0xA0` false — and a packed
`ZoneOffset` at 18-19). Only the Xlink layout above applies to this repo's TCP path; the Telink
one is the BTLE-direct path and is noted here only so the two are not confused.

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
3. ~~Group control needs real protocol research, not just address-targeting~~ — **resolved and
   wired in**: `target_id`/`sub_id` were never independent fields, together they already form the
   outer envelope's 2-byte MeshAddress, so group addresses need zero `PacketBuilder` changes - see
   the Groups section above's second correction. `cync_lan.experimental_set_group_power` is wired
   in and needs real-hardware testing to confirm device firmware actually honors a group-range
   target, same as every other experimental command in this doc.
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
