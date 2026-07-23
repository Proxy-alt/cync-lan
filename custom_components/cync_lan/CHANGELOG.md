# Changelog

Version history for the Home Assistant `cync_lan` custom_component
specifically (`custom_components/cync_lan/manifest.json`'s `version` field).
This is independent of the underlying [`cync-lan`](https://github.com/Proxy-alt/cync-lan)
Python package's own version scheme (`0.0.6bNN`, see the parent project's
root `CHANGELOG.md`) - the two are versioned and released separately, even
though this integration depends on that package to do the actual protocol
work.

### 0.4.0

- Add `CyncDevice.set_multicolor_gradient_mode()`/`set_multicolor_segment_count()`/
  `set_multicolor_segments()` in the underlying package - 3 confirmed wire
  primitives for programming a custom MultiColor scheme (per-segment RGB
  color on segmented/dynamic light strips), plus standalone
  `experimental_set_multicolor_gradient_mode`/
  `experimental_set_multicolor_segment_count`/
  `experimental_set_multicolor_segments` services. Per-segment position and
  color are independently optional, confirmed directly from the app's own
  data model. This integration does not orchestrate the full multi-send
  sequence a real custom scheme needs (chunking/ordering for more than 2
  segments) - only the 3 confirmed primitives are exposed; see the
  README's "Known limitations" for what that means in practice. Two
  related commands (`SetMultiColorSchemeDirectCommand`'s "entertainment"/
  live-streaming variant, `SetMultiColorBitmapCommand`'s tile/matrix
  bitmap data) remain out of scope - both dispatch via methods never
  traced anywhere else in this project's research, not just untested.
- Add a dedicated, always-on `experimental_features.log` (no flag to
  enable) that records every single `experimental_*` command/service
  invocation the moment it runs, independent of the console warnings that
  only fire once per process per command name - attach this file (not the
  full HA log) when reporting a bug about any experimental feature. Never
  blocks the actual command if the log file itself can't be created (e.g.
  a permissions issue) - falls back to a one-time error in the main log
  instead.

### 0.3.0

- Add `CyncDevice.remove_from_scene()` in the underlying package - the
  counterpart to `add_to_scene()`, removing one device's captured state
  from an existing scene without deleting the scene or affecting its other
  members. Confirmed via decompiled app source
  (`RemoveDeviceSceneCommand`); unlike `add_to_scene()`, both device
  product-family code paths are dispatch-confirmed here, not just the
  common one.
- Add `experimental_add_device_to_scene` and
  `experimental_remove_device_from_scene`: standalone versions of the same
  functions `experimental_push_automation_to_hardware` already uses
  internally, so they can be tested against a scene you already have
  (Cync-app-created or otherwise) without going through the full
  automation-push flow.

### 0.2.0

- Add native Scene/Schedule creation: `create_scene`, `create_schedule`, and
  `add_automation` in the underlying package, plus
  `CyncDevice.add_to_scene()`, using payloads traced from the real Cync
  Android app's decompiled source. These are the first commands that read a
  response back off the wire - the hub allocates a scene/schedule ID and
  reports it back asynchronously over a distinct notification channel,
  decoded by a new HDLC/PPP frame parser. Whether that notification channel
  rides the same connection this integration already intercepts is
  unconfirmed, so both functions return `None` on a 10-second timeout
  instead of raising.
- Add `experimental_push_automation_to_hardware`: reads an existing HA
  automation's own trigger/condition/action config and pushes it onto the
  Cync hub as a native Scene + Schedule, so the same schedule keeps firing
  even if Home Assistant or the network goes down. Strictly scoped to a
  plain time trigger, an optional day-of-week condition, and
  `light.turn_on` actions targeting Cync lights only with a single color -
  brightness, effects, light groups, and anything else a Cync Scene can't
  represent are rejected outright rather than silently dropped. See the
  README's "Pushing an HA automation to Cync hardware" section.
- Add `CHANGELOG.md` (this file) and a `RELEASING.md` release process for
  the maintainer - this integration had no version history or HACS release
  process until now, despite `manifest.json` sitting at `0.1.0` since its
  first commit.

### 0.1.0

Initial buildout - never actually tagged/released via HACS despite the
version number; consolidated here retroactively from the full commit
history now that a real release process exists.

**Core integration**

- Config flow: connect a Cync account (email/password + emailed one-time
  code), confirm the device count, and start the local TCP/TLS listener -
  no YAML configuration.
- Reauthentication flow triggered automatically when the cached cloud
  session can't be silently refreshed.
- Options flow for local port and the automatic device-list export refresh
  interval.
- Auto-generates a self-signed TLS certificate and points the underlying
  package's config/state directory at Home Assistant's own config
  directory, instead of requiring manual certificate/path setup.
- Vendors the `cync-lan` package's protocol code directly into the
  integration (`custom_components/cync_lan/vendor/`) to work around Home
  Assistant's stricter dependency-resolution requirements, kept in sync
  with the upstream package by hand.
- Diagnostic entities and repair/reconfigure flows for common failure
  modes: TCP server crashes, stale sessions, connection churn, and
  cloud-export token-cache instability.

**Entities**

- `light`: on/off, brightness, color temperature, RGB, and effects, per
  device capability.
- `light` (group, opt-in): one aggregate entity per Cync device group,
  built on Home Assistant's own group-light helper, with an option to hide
  individual group members from dashboards.
- `switch`: on/off switches and outlets/plugs, plus one entity per saved
  Cync Schedule to enable/disable it.
- `fan`: fan controllers with percentage and preset-speed control.
- `binary_sensor`: standalone motion/occupancy sensor accessories, a
  secondary motion entity on light/switch models with a built-in
  occupancy sensor, and a Wi-Fi-presence occupancy sensor from the app's
  own TCP login packets.
- `select`/`number`/`switch` (config): indicator-LED mode, color,
  brightness, and blink-on-WiFi-disconnect - confirmed working on real
  hardware.
- `sensor` (diagnostic): one entity per native Cync motion-sensor schedule
  slot, read-only.
- `scene`: one activatable entity per saved Cync Scene.

**Groups, Scenes, and Schedules (experimental services)**

- `experimental_set_indicator_led`, `experimental_set_motion_sensor_settings`
  (plus a guided "Edit Motion Sensor Settings" options-flow wizard that
  walks through waking the physical sensor first),
  `experimental_set_motion_sensor_schedule`.
- `experimental_execute_scene`, `experimental_delete_scene`,
  `experimental_delete_schedule`, `experimental_toggle_automation`.
- `experimental_set_group_power`, `experimental_set_group_membership`.
- All prefixed `experimental_` as a visible risk signal - most of these
  commands' outer envelope byte is a predicted value pending real-hardware
  confirmation, tracked per-command in the parent project's
  `docs/mesh_opcodes.md`.

**Reliability fixes found along the way**

- Command-ack callbacks not seeding initial entity state.
- State updates never reaching the HA frontend because a callback wasn't
  marked `@callback`.
- Dimmable switches losing brightness control after being reclassified from
  `light` to `switch`.
- Rejected devices being silently resurrected due to a missing `await`.
- Blocking file I/O on the event loop during config/group parsing and cloud
  export writes.
- A singleton-state bug across repeated integration reloads.
