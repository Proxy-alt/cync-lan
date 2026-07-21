# Cync-native automations: scenes, schedules, and motion-sensor schedules

Answers the question "can we sync automations between Cync and Home Assistant" with real data
(a live user's `raw_mesh.cync` export) and decompiled-app research. Sourcing conventions match
[mesh_opcodes.md](mesh_opcodes.md): **confirmed** (exact class/line citation or real data),
**plausible** (inferred, not proven), **not found** (explicit negative, not guessed).

## The short answer

Cync's own "Routines" feature is narrower than it sounds: it's just **Scenes** (named multi-device
state snapshots) and **Schedules** (time+day-of-week triggers that fire a scene) — confirmed no
general trigger/condition/action automation engine exists anywhere in the app. Separately, each
motion-sensor-equipped **group** has its own **motion-sensor schedule** (4 fixed time-of-day slots
controlling what a sensor-triggered light does) — a third, distinct mechanism, unrelated to
Scenes/Schedules despite the similar name.

Home Assistant's automation model is far more expressive than any of these three. A general
"sync HA automations to Cync" feature doesn't have anywhere near enough Cync-side automation
concept to sync *into* for most HA automations - most HA automations simply have no Cync
equivalent to become. The one piece that's both genuinely Cync-native and genuinely useful to
sync bidirectionally is the narrower **motion-sensor schedule** - and unlike scenes/schedules
(cloud-only, unverified write path), it turns out to write over the **local mesh**, not the cloud.

## Real data: this account's own export

A live user's `raw_mesh.cync` (the raw cloud export `cloud_api.py`'s `_parse_raw_export()` writes
before transforming it into cync-lan's own config, see `src/cync_lan/cloud_api.py:~596`) has, per
home:

```
properties.groupsArray[].sensorSchedules   # motion-sensor schedules, per group
properties.sceneArray                      # Scenes (empty on this account)
properties.schedules                       # Schedules (empty on this account)
properties.ftsModel                        # {am, day, pm, sleep, wakeUp} - see below
```

This account has **7 of 34 groups with real, populated `sensorSchedules`** (all motion/occupancy-
sensor-equipped devices - e.g. "Utility Room", "Garage", "Mudroom", "Master Closet"), each with
exactly 4 schedule blocks. `sceneArray`/`schedules` are both empty - this account doesn't use
those two app features, though the data model below is confirmed from decompiled-app code
regardless of that.

## Motion-sensor schedules — fully decoded

Real example (one group, all 4 slots):

```json
[
  {"brightness": 100, "cct": 1, "displayName": "", "endTime": "2021-05-30 08:59", "id": 1, "isEnabled": true, "simpleMode": true, "startTime": "2021-05-30 06:00"},
  {"brightness": 100, "cct": 1, "displayName": "", "endTime": "2021-05-30 18:59", "id": 1, "isEnabled": true, "simpleMode": true, "startTime": "2021-05-30 09:00"},
  {"brightness": 100, "cct": 1, "displayName": "", "endTime": "2021-05-30 20:59", "id": 2, "isEnabled": true, "simpleMode": true, "startTime": "2021-05-30 19:00"},
  {"brightness": 100, "cct": 1, "displayName": "", "endTime": "2021-05-30 05:59", "id": 3, "isEnabled": true, "simpleMode": true, "startTime": "2021-05-30 21:00"}
]
```

- **Confirmed** wire/JSON shape: `SensorSchedule.java:14-111` (`com.gelighting.cbygekit.services.
  locations`), fields match 1:1. Parsed into a domain model via `MappersKt.s()`
  (`services/locations/MappersKt.java:1880-1896`).
- **`id`** (confirmed, `SensorScheduleId.java:27,40-48`): range 0-3, keys a fixed 4-slot
  `ScheduleTimeSlot` enum (`ScheduleTimeSlot.java:26-33`): `0=Morning, 1=Daytime, 2=Evening,
  3=Sleep` (names confirmed via `SensorSchedule2Mapper.java:41-52`). `GESensorSchedule2Validator.
  java:43-66` enforces all 4 slots must tile a full 24h day, edges aligned within 6 minutes -
  exactly matching the 7 real groups above (each has exactly 4 blocks covering 24h).
- **`cct`** (confirmed, `CctColor.java:39-45`): a raw **0-100 warm→cool percentage**, not an index
  into anything - `0` = warmest, `100` = coolest. `cct: 1` in the real data above is essentially
  the warmest possible white. **Not** an `ftsModel` index (see below).
- **`simpleMode`** (confirmed, `SensorSchedule2Mapper.java:75`): selects a
  `MotionSensorResponseMode`: `isEnabled=false` → `DISABLED`; `isEnabled=true, simpleMode=true` →
  `SIMPLE`; `isEnabled=true, simpleMode=false` → `OCCUPANCY` (an "advanced" mode with additional
  fields not present in the simple JSON shape above - `VACANCY` exists as a 4th enum value but
  wasn't traced to a reachable UI path).
- **Real-world data-quality caveat, worth flagging for any code parsing raw exports**: this
  account's real `id` values are `1, 1, 2, 3` - a duplicate `1` and no `0` (Morning) at all. The
  app's own `SensorSchedule2Mapper.b()` builds a `Map<ScheduleTimeSlot, Item>` from this list,
  which silently drops the duplicate (last-write-wins) - meaning even the real Cync app would show
  no "Morning" schedule configured for this account. Don't assume real export data is
  internally consistent; validate before trusting `id` uniqueness.

## `ftsModel` — a dead end, not what it looks like

`{"am": "07:00 AM", "day": "11:00 AM", "pm": "03:00 PM", "sleep": "11:00 PM", "wakeUp": "07:00 AM"}`
looks like it should be the clock-time boundaries the 4 schedule slots above key into. It is not,
at least not in this app version: **no `ftsModel`/`FtsModel`/`wakeUp` field exists anywhere** in
`com.gelighting.cbygekit.*` (grepped across the entire decompiled tree) - the only `wakeUp` hits
are an unrelated `DeviceSettingsWakeUpFragment`. **Not found** - likely a vestigial or legacy cloud
field the current app version no longer parses, possibly superseded by hardcoded slot names
(Morning/Daytime/Evening/Sleep) rather than user-configurable boundary times. Not worth building
around; if cync-lan ever surfaces motion-sensor schedules, use the fixed slot names, not this
field.

## Scenes and Schedules — the real "Routines" feature, confirmed narrow

`RoutinesControlTab.java:24-27` is the entire "Routines" tab: a two-value enum, `Scenes` (0) and
`Schedules` (1). That's it - no trigger/condition/action UI exists anywhere under
`com.savantsystems.oneapp`. (A bundled Tuya/ThingClips scene-rule engine exists in the APK -
`com/thingclips/scene/core/*`, `ConditionFactory`, `Rule`, `RuleType`, ~5000 files - but it's
**confirmed dead code for Cync's purposes**: the only cross-references from Cync/Savant code are
imports of a generic string-constant class, not the rule engine itself. Reads as a transitive
dependency from a shared multi-brand Savant build, not an active Cync feature.)

- **Scene** (confirmed, `SceneModel.java`): a named set of `SceneActionModel` entries, each pinning
  one device (by `DeviceId`+`MeshAddress`) to a captured state (`SceneDeviceStateComponent`) - a
  multi-device state snapshot, triggered on demand.
- **Schedule** (confirmed, `ScheduleModel.java`): `{name, enabled, ScheduleTime startTime,
  Set<ScheduleDay>, sceneIdValue}` - a plain time+day-of-week trigger that fires a scene by ID.
  Unrelated to motion sensors.
- Both are empty on this real account (it doesn't use these two features), but the data model is
  confirmed from app source regardless - other accounts will have these populated. Worth capturing
  in `_parse_raw_export()` and exposing as HA `scene.*` entities (from `sceneArray`) if/when
  cync-lan wants this - not analyzed further here since this account has no real example to
  validate parsing against.

## `isSubgroup` — a real functional hierarchy, not UI-only

Confirmed, `SubgroupModel.java`: field `b` = parent `groupId` (int), plus its own `MeshAddress`
and its own independent `sensorSchedules` list (a subgroup's motion schedule is separate from its
parent's). `GroupPolicyKt.java`'s `a()` shows devices get commanded onto *both* the group's and
subgroup's mesh addresses, with distinct `GroupReachFlag` semantics depending on policy. cync-lan's
existing group parsing already works for subgroups (they get their own group ID/address per the
Groups section of `mesh_opcodes.md`), but doesn't currently capture the parent link - worth adding
if a nested HA area/group hierarchy is ever wanted, rather than flattening every group to one
level.

## Bidirectional sync: what's actually feasible

**Cync → HA (reading)**: straightforward for all three mechanisms - parsing already-exported data,
no cloud dependency needed. Motion-sensor schedules are the most immediately useful (real,
populated data on this account); Scenes/Schedules need a test account with them populated to
validate parsing against.

**HA → Cync (writing) — the actual finding that matters here**: for motion-sensor schedules,
writing does **not** require a cloud REST API call. `SetMotionSensorScheduleCommand.java:85-193`
encodes the schedule as a **local mesh opcode packet** (`0xF7 0x11 0x02 0x0B` - see
[mesh_opcodes.md](mesh_opcodes.md#motion-sensor-schedule-write--op_code--0xf7-sub-command-0x0b)
for the full payload), dispatched through the exact same command-delegate abstraction
(`XlinkCommandDelegate` for WiFi/TCP mesh) that cync-lan already speaks for ordinary light control.
This is architecturally identical to every opcode cync-lan already reverse-engineers - **a
genuinely good fit for this project's local-only design**, not a forced round-trip through Cync's
cloud.

**UPDATE, later session: Scenes/Schedules are local-mesh-writable too - this was wrong.** A
dedicated pass traced the actual create/edit/delete code path
(`com/gelighting/cbygekit/services/scenes/RoutinesService.java`) and found it never touches HTTP -
every write builds a command object and dispatches it through the same `XlinkCommandDelegate`/
`TelinkCommandDelegate` mesh-command machinery as everything else in this doc:

| Command | Opcode | Role |
|---|---|---|
| `CreateSceneHubCommand` | `HUB_CREATE_SCENE = 0x10` | Create a scene (name + icon ID) |
| `DeleteSceneHubCommand` | `HUB_DELETE_SCENE = 0x1F` | Delete a scene |
| `CreateScheduleHubCommand` | `HUB_CREATE_SCHEDULE = 0x92` | Create a schedule |
| `AddDeviceSceneCommand` | payload prefix `0xEE,0x11,0x02` | Add one device's captured state to a scene (sceneId, mode, brightness/CCT/RGB, fade) |
| `ToggleAutomationCommand`/`ToggleAutomationHubCommand`, `RemoveDeviceSceneCommand`, `DeleteScheduleHubCommand` | (same family) | Enable/disable, remove a device from a scene, delete a schedule |

(Opcodes confirmed at `com/gelighting/cbygekit/services/devices/xlink/XlinkCommandCode.java:46-51`.)
A grep for `retrofit2`/`@GET`/`@POST`/`@PUT`/`@DELETE` across `services/scenes/`, `services/
schedules/`, and `services/devices/command/` came back completely empty. `SceneRepository.java`
persists purely to on-device Room/SQLite (`SceneDao`/`CoreDatabase`), not a network layer. The
`sceneArray`/`schedules` fields already seen in real cloud exports exist because the **hub**
telemeters its own mesh state up to the cloud independently - not because the app writes scenes
through a REST call.

**UPDATE: op_code dispatch verified, and it's not the 0x8E bug for the pure Hub commands.**
`CreateSceneHubCommand`/`DeleteSceneHubCommand`/`CreateScheduleHubCommand`/
`DeleteScheduleHubCommand`/`ToggleAutomationHubCommand` each build their own complete wire frame
directly and pass it to a third dispatch method (`mo14053e`) that does no envelope construction of
its own - just posts the already-framed bytes. The `XlinkCommandCode` values
(`HUB_CREATE_SCENE=0x10`, `HUB_DELETE_SCENE=0x1F`, `HUB_CREATE_SCHEDULE=0x92`,
`HUB_DELETE_SCHEDULE=0x94`, `HUB_TOGGLE_AUTOMATION=0x93`) are the real outer op_codes, written
verbatim - genuinely different from the `SetStatusIndicatorSettingsCommand`-style bug, not another
instance of it. None of these five use a `MeshAddress` target at all (accepted as a parameter,
unused in every body read) - consistent with being hub-scoped/home-wide, not per-device.

**CORRECTION, follow-up pass**: `DeleteSceneHubCommand`/`DeleteScheduleHubCommand`/
`ToggleAutomationHubCommand` all call `XlinkTranslatorKt.m14449a()` only -
**`Frame.m14440a()` is never actually invoked by these 3 files**, contradicting the "two
structurally identical framers" claim just above; not yet re-checked for `CreateSceneHubCommand`/
`CreateScheduleHubCommand` specifically. `XlinkTranslatorKt.m14449a()` → `Xlink.m14391a()`
(`Xlink.java:24-71`) builds `msgId(4B LE) + flagByte(1B const) + op_code(1B) + len(2B LE) + payload
+ checksum(1B)`, then **0x7D/0x7E byte-stuffs and wraps in `0x7E...0x7E`** - a PPP/HDLC-style
escaped frame, structurally unlike cync-lan's own confirmed 5-byte-header TCP wire format (no
delimiters/escaping there) documented in `mesh_opcodes.md`'s "TCP relay envelope research" section -
which already flagged this exact `Xlink`/`XlinkTranslatorKt` code as `@Deprecated`, "the phone-app's
older command channel, not necessarily byte-identical to the device-facing protocol cync-lan
replicates."

**This is a real, unresolved architectural question, not just a missing byte count**: it's unknown
whether these 3 (and presumably the 2 Create commands) actually ride over the same TCP relay
cync-lan speaks at all, wrapped in cync-lan's own `PacketBuilder` envelope like every other
command in this codebase - or whether they're BLE-GATT-specific and would need this raw
HDLC-escaped frame built and sent as a self-contained unit, bypassing `PacketBuilder` entirely.
Confirmed payloads for the 3 read this pass (all via `WriteBuffer`'s fixed-width write methods -
`m14441a`=1B, `m14443c`=4B LE, `m14444d`=2B LE, no generic/templated width ambiguity):
- `DeleteSceneHubCommand` (`:63-67`): 2 bytes, `sceneId` as **uint16 LE** - note this contradicts
  the existing `experimental_execute_scene` service's 1-byte `scene_id` (0-255) assumption; for
  *this* command the same underlying field is written 2-byte-wide, not 1.
- `DeleteScheduleHubCommand` (`:63-67`): 2 bytes, `scheduleId` as uint16 LE.
- `ToggleAutomationHubCommand` (`:93-100`): 52 bytes total - `scheduleId`(2B LE) + `sceneId`(**4B
  LE** - the same field written 2 different widths across these two commands, a real app-code
  quirk, not an assumption) + 26 zero-padding bytes + a redundant zero u16 + `enabled`(1B, 0/1) +
  1 zero byte + 16 zero bytes.

**UPDATE, wired in as an experiment**: despite the transport question above remaining unresolved,
these 3 (`delete_scene`/`delete_schedule`/`toggle_automation` in `devices.py`, exposed as
`cync_lan.experimental_delete_scene`/`experimental_delete_schedule`/
`experimental_toggle_automation`) are now wired in, sending the confirmed payload bytes through
cync-lan's own `PacketBuilder`/TCP envelope as a working hypothesis - explicitly at the user's
request, to be tested against real hardware rather than settled by more static analysis.
`_warn_experimental_transport_unconfirmed` logs once per command name on first use to keep the
uncertainty visible at runtime, not just in this doc. This genuinely still needs a live packet
capture (does a real "delete scene" action from the app produce anything resembling
`PacketBuilder`'s format at all, over the TCP relay cync-lan intercepts?) to *confirm* rather than
just hypothesize - the open question is about which transport carries this command in practice,
which static analysis of the phone app's Kotlin/Java source can't settle on its own. If real
hardware testing shows these are no-ops or provoke errors, that itself is the answer: the app's
older HDLC-framed channel and cync-lan's TCP relay are genuinely different paths for this specific
command family.

**UPDATE, follow-up research on `toggle_automation` specifically**: traced its one real call site,
`RoutinesService.applyScheduleEnabled()` - it picks between `ToggleAutomationHubCommand` (the
payload `toggle_automation()` implements) and a sibling `ToggleAutomationCommand` based on whether
a WiFi hub device controller exists for the schedule's location, not a version/feature gate. The
Hub-command branch dispatches through `hubDeviceController.mo14149i()` - the exact same call path
already used by `CreateSceneHubCommand`/`CreateScheduleHubCommand`/`DeleteSceneHubCommand`/
`DeleteScheduleHubCommand` elsewhere in that same file. Since cync-lan's own target hardware (WiFi
bulbs/plugs bridging BLE mesh devices) always has a WiFi hub in this sense, `ToggleAutomationHubCommand`
is confirmed to be the branch that applies - `ToggleAutomationCommand` (a different, simpler,
`0x8E`-bug-family command with a 5-byte payload, not the ambiguous-transport family) is only reached
when there's no WiFi hub for a location, a topology cync-lan never sits in front of, so it isn't
worth implementing separately. This raises confidence in `toggle_automation()`'s payload by
structural analogy to the other 2 already-wired Hub commands sharing the identical dispatch path -
**it does not independently confirm the actual wire bytes** (still built via the PPP/HDLC-style
`Xlink.a()` framer, still not proven to ride the same TCP relay cync-lan intercepts). Treat this as
the same confidence level as `delete_scene`/`delete_schedule`, not as a fully resolved transport
question.

`AddDeviceSceneCommand`/`RemoveDeviceSceneCommand` (adding/removing one device's captured state
within a scene) are different and dual-path, branching on the target device's product type: the
"regular" product path **does** have the exact `0x8E`-relay bug (the `0xEE,0x11,0x02,...` array
misread as an op_code, same fix class as indicator LED - not yet applied); a "special" product-type
path calls the explicit-op_code method directly with the real op `0xEE`, no bug. See
`mesh_opcodes.md`'s "CORRECTION" section for the general bug pattern this partially replicates.

**UPDATE, follow-up pass**: full payload resolved, prompted by researching the Schedule "fade"
feature (a gradual-brightness-transition option in the app's Schedule UI) - it turns out fade
lives entirely inside `AddDeviceSceneCommand`'s own payload, not on the Schedule command itself.
See `mesh_opcodes.md`'s "Full light-run-mode"-adjacent section for the exact byte layout (13-byte
non-hub-routed payload ending in `[fadeByte, 0xFF]`; a structurally different manually-built
`WriteBuffer`+`FrameCode` payload for hub-routed devices) and `ScheduleFade.java`'s 8-value coded
duration enum. Confirmed hardware-side (the bulb fades autonomously using the byte it received at
scene-programming time) via `ExecuteSceneCommand` never resending color/fade data when a scene
actually fires. This means implementing fade requires implementing `AddDeviceSceneCommand` itself
first (full per-device scene-slot programming: mode/brightness/color, not just the fade byte) -
still unblocked by the Hub-commands transport question per the reasoning below, but a materially
bigger feature than "add a field."

## Recommendation

The "sync an HA automation back to Cync" theory, as generally framed, doesn't hold up - there's no
general automation concept on the Cync side for most HA automations to become. But there are now two
concrete, locally-writable features worth building on their own merits:

1. **Read motion-sensor schedules from the cloud export** and expose them in HA - **done**, one
   diagnostic sensor entity per schedule slot (`custom_components/cync_lan/sensor.py`).
2. ~~Write motion-sensor schedules over the local mesh~~ - **done**: `devices.py`'s
   `set_motion_sensor_schedule()`, exposed as `cync_lan.experimental_set_motion_sensor_schedule` -
   see `mesh_opcodes.md`'s "Motion-sensor schedule write" section for the full resolved byte format.
   Not yet tested against real hardware.
3. **Scenes/Schedules (the "Routines" tab) are also locally writable**, not cloud-only as earlier
   thought - `HUB_CREATE_SCENE`/`HUB_CREATE_SCHEDULE`/`AddDeviceSceneCommand`/etc. (see above).
   **UPDATE: op_code dispatch verified (no `0x8E` bug for the pure Hub commands), exact payloads
   resolved for 3 of the 5, and those 3 are now wired in as a real-hardware experiment despite a
   deeper, still-unresolved transport question.** `DeleteSceneHubCommand`/
   `DeleteScheduleHubCommand`/`ToggleAutomationHubCommand` build their own complete wire frame via
   `XlinkTranslatorKt.m14449a()`/`Xlink.m14391a()` - a PPP/HDLC-style, `0x7E`-delimited-and-
   byte-stuffed frame, structurally unlike cync-lan's own confirmed TCP wire format, and traced to
   the exact `Xlink`/`XlinkTranslatorKt` code `mesh_opcodes.md` already flagged `@Deprecated` as
   possibly the phone app's *older* command channel. **Whether these commands ride over the same
   TCP relay cync-lan intercepts at all, or are BLE-GATT-specific, is genuinely unresolved** - not
   knowable from static source reading, needs a real packet capture. Confirmed payload byte
   layouts (see "HA → Cync (writing)" above for exact `WriteBuffer` field widths) are sent through
   cync-lan's own confirmed `PacketBuilder` envelope anyway, exposed as
   `cync_lan.experimental_delete_scene`/`experimental_delete_schedule`/
   `experimental_toggle_automation`, so real hardware can settle the transport question by
   observation instead of waiting on a packet capture first.
   `CreateSceneHubCommand`/`CreateScheduleHubCommand`
   need their own payload research on top of that (not yet done - `String30` name encoding, full
   schedule field layout). `AddDeviceSceneCommand`/`RemoveDeviceSceneCommand` (adding/removing one
   device's captured state within a scene) are a separate case: dual-path depending on the target
   device's product type, and the "regular" path **does** have the exact `0x8E`-relay bug (payload
   `0xEE,0x11,0x02,...` misread as an op_code) - same fix class as indicator LED, not yet applied,
   and not blocked by the transport question above since it's confirmed to go through the same
   `mo14054f`/`mo14056h` methods already proven to carry real TCP-relay traffic. Full payload
   (including the Schedule "fade" feature) since resolved - see `mesh_opcodes.md`'s follow-up on
   this section.
4. **Read Scenes/Schedules from the cloud export** and expose them in HA - **done**: `scene.py`'s
   activatable scene entities and `switch.py`'s schedule enable/disable switches, replacing the raw
   `experimental_execute_scene`/`experimental_toggle_automation` services as the primary surface
   (both services remain registered for existing automations/scripts). Raw JSON field names
   resolved via `LocationProperties`'s kotlinx.serialization descriptor (not the internal Kotlin
   domain model's field names, which differ - same lesson as `groupsArray`): top-level
   `properties.sceneArray`/`properties.schedules` (confirmed exact keys, not `scenesArray`/
   `schedulesArray`); a scene entry's `sceneID`/`displayName`; a schedule entry's `scheduleID`
   (falling back to the sibling `id` field - both present with no confirmed distinction) /
   `displayName` / `trigger.action.sceneID` (the scene it triggers) / `state` (inferred, not
   confirmed, to be the enabled flag - the closest boolean field on the DTO). **UNVALIDATED against
   a real populated export** - the one real account sampled for this research has zero scenes/
   schedules configured, so none of this has been cross-checked against real captured JSON the way
   `groupsArray` was. See `cloud_api.py`'s `parse_scenes()`/`parse_schedules()` docstrings.

Motion-sensor schedule writing, Scenes/Schedules *writing* (delete/toggle, wired as an experiment),
and Scenes/Schedules *reading* (as real entities) are all implemented now - Scene/Schedule
*creation* from HA (`CreateSceneHubCommand`/`CreateScheduleHubCommand`/`AddDeviceSceneCommand`'s
full payload) is the one piece from this doc's original research still unbuilt.
