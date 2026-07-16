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
cloud. Scenes/Schedules were not analyzed for a write opcode in this pass - would need separate
verification before extending this same claim to them (and Scenes in particular, being pure
app-side multi-device snapshots rather than a single device setting, may only ever be
writable via the cloud - unconfirmed either way).

## Recommendation

The "sync an HA automation back to Cync" theory, as generally framed, doesn't hold up - there's no
general automation concept on the Cync side for most HA automations to become. But there is one
concrete, well-scoped, locally-writable feature here worth building on its own merits:

1. **Read motion-sensor schedules from the cloud export** and expose them in HA (informational at
   minimum - "this group's motion sensor uses these 4 time-of-day brightness/color settings").
2. **Write motion-sensor schedules over the local mesh** (`0xF7 0x11 0x02 0x0B`, `cmd_code` via the
   length formula in `mesh_opcodes.md`) - this is the piece that would let an HA-side UI/automation
   actually change a Cync group's native motion-sensor behavior, no cloud call needed.
3. Scenes/Schedules (the cloud-only "Routines" tab) are a separate, larger effort with an unverified
   write path - worth deprioritizing behind the above.

Neither is implemented yet - this doc establishes what's confirmed and buildable, not a finished
feature.
