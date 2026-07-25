# Changelog

Version history for the Home Assistant `cync_lan` custom_component
specifically (`custom_components/cync_lan/manifest.json`'s `version` field).
This is independent of the [`cync-lan`](https://github.com/Proxy-alt/cync-lan/tree/core)
core protocol library's own version scheme and the
[`cync-lan-mqtt`](https://github.com/Proxy-alt/cync-lan/tree/python)
Docker/MQTT add-on's own version scheme - all three are versioned and
released separately, even though this integration depends on `cync-lan` to
do the actual protocol work.

### 2.1.0

Requires `cync-lan` 0.3.0.

**New: five more protocol commands, all reachable from the UI** (with
experimental commands enabled). On the **Cync LAN Bridge** device:

| Entity | What it does |
|---|---|
| **Hub firmware version** | Asks the hub what firmware it is running, plus its MAC and setup code as attributes. Distinct from the version on each device page, which comes from the cloud export - a mismatch means the export is stale. |
| **Hub clock** | The date and time the hub thinks it is. Native Cync Schedules fire off *this* clock, not Home Assistant's, so a hub whose time has drifted runs its automations at the wrong moment. Nothing else showed that. |
| **Delete automation binding for *&lt;schedule&gt;*** | Removes what makes a Schedule fire, without deleting the Schedule itself. There was previously no way to do this at all. |
| **Delete group *&lt;name&gt;*** | Deletes a Cync device group from the mesh. |

The two query sensors are **disabled by default** - each poll puts a real
command on the mesh, and neither value changes quickly. The two delete
buttons are disabled by default for the same reason the existing ones are:
Home Assistant has no "are you sure?" for a button press.

A query that times out keeps its previous value rather than blanking. These
commands' reply channel is unconfirmed, so an occasional miss is expected and
should not look like the hub disappeared.

**Deliberately not included.** Three commands in the protocol flash device
firmware (`StartHubFirmwareUpdates` and two Wi-Fi OTA siblings). Every other
experimental command here fails safe - a wrong predicted envelope byte means
the device ignores the packet - but these do not, so no code path capable of
sending them exists. A fourth, "query available firmware updates", is also
left out: its reply is a variable-length structure that would produce
plausible-looking nonsense if decoded wrongly, and there is no capture to
check a decoder against. Both are documented in `docs/mesh_opcodes.md`.

### 2.0.0

**Breaking: the `experimental_*` actions are now opt-in.** If you call any of
them from an automation or script, enable them first or those calls will fail:

> Settings -> Devices & services -> Cync LAN -> **Configure** -> General
> settings -> **"Enable experimental commands (advanced)"**

They are off by default because every one sends a mesh command whose wire
format is *predicted* from a length formula rather than confirmed against a
real packet capture, and most have never been exercised on real hardware.
Previously they sat in Developer Tools and the automation picker looking as
ordinary as any other action, with only the name prefix as a warning.

**Requires `cync-lan` 0.2.1.** Earlier builds of that library pinned `pyyaml`
to exactly `6.0.2` while Home Assistant requires `PyYAML==6.0.3`, so this
integration's requirements could not be installed at all. 0.2.1 relaxes the
pin. It also brings a fix worth knowing about: six hub
commands (create/delete scene, create/delete schedule, add/toggle automation)
were sending a length field one byte short, so device firmware read a
truncated body and the command silently did nothing. If you tried these and
nothing happened, that is why. They are still experimental - the framing is
correct now, which is not the same as confirmed working.

**Fixed: reauthentication could never complete.** When the cached cloud token
expired, the reauth flow crashed on its final step - Home Assistant rejects
creating a new entry from a reauth flow, which is what this did. It now
updates the existing entry in place. Anyone who hit an expired token and could
not get back in should be able to now.

**Fixed: a newly-added device was ignored for the first 15 minutes after a
reboot.** The "re-export as soon as a new device appears" cooldown compared
against a marker initialised to zero, and `time.monotonic()` counts from boot
on Linux - so for the first 15 minutes of uptime the check suppressed the very
first trigger. A rebooted Raspberry Pi or a restarted HA OS would quietly skip
a device it had just discovered.

**Fixed: several operations blocked Home Assistant's event loop**, which shows
up as the whole instance stuttering: creating the config directory, checking
the export file, and the periodic refresh all did file I/O directly on the
loop.

**Fixed:** command acknowledgements for brightness, colour temperature and RGB
ignored the sub-device id, so on a multi-outlet device they could update the
wrong entity's state.

**New: every experimental command is now reachable from the UI**, instead of
only from Developer Tools where each one wanted a numeric scene or group ID
that the Cync app never shows you. With the option enabled:

- **Buttons** on the Cync LAN Bridge device: *Query mesh credentials*, and a
  *Delete scene* / *Delete schedule* button per scene and schedule, each
  carrying its own ID. The delete buttons arrive **disabled** - Home Assistant
  has no "are you sure?" dialog for a button, and recreating a deleted scene
  means going back to the phone app, so enabling the entity is the
  confirmation step.
- **Entities** for the commands that have state: group power (per group), and
  MultiColor gradient mode and segment count (per colour-capable device).
- **Guided forms** under Configure -> Experimental commands for the rest -
  push an automation to the hub, add or remove a device in a scene or group,
  write a motion-sensor schedule slot, and program MultiColor segments. These
  take several parameters and have nothing to read back, so a form with real
  device/scene/group pickers fits better than an entity.

**New action: `experimental_query_mesh_credentials`** reads your BTLE mesh name
and password from a connected hub. These are what `cync-lan-ble-provision`
needs to add a new device to your *existing* mesh rather than only a
factory-default one. The password is your mesh's shared secret, so it is
returned as action response data (or shown in a dismissible notification from
the button) rather than written to the log. Experimental: the response channel
is unconfirmed and may simply time out on your hardware.

Internal: the test suite could not be collected at all - a misplaced pytest
declaration meant all 461 tests had been silently not running, and no workflow
ran them either. Both fixed; tests and type checks now run on every push and
pull request.

### 1.4.0

- Add Bluetooth discovery for factory-default (never-provisioned) Cync
  devices (`manifest.json`'s new `"bluetooth"` matcher: local name
  `telink_mesh1`, Telink's manufacturer ID). Unlike DHCP discovery, this
  does **not** lead into the account-setup flow - a factory-fresh device
  isn't part of any Cync account yet, so doing that would be premature.
  Instead it shows an informational step pointing at the official Cync
  app or the `cync-lan-ble-provision` CLI tool. Doesn't declare
  `"bluetooth"` as a manifest dependency, since this integration never
  calls the runtime bluetooth API - only receives the discovery match.
  See `quality_scale.yaml`'s `discovery` entry.

### 1.3.0

- Add DHCP discovery: Home Assistant now proactively offers to set up this
  integration when a device with a Cync-pattern DHCP hostname (`GE_*`)
  appears on the network, instead of requiring you to find it yourself in
  **Add Integration**. Still leads to the same account-credentials form -
  this integration is set up per Cync account, not per device, so
  discovery is a nudge into setup, not a replacement for entering your
  Cync account email/password. See `quality_scale.yaml`'s `discovery`
  entry.

### 1.2.0

- Pass `mypy --strict` cleanly (see `mypy.ini`) - quality_scale.yaml's
  `strict-typing` (platinum) is now done. No functional change to this
  integration's own code, aside from bumping the `cync-lan` dependency to
  `>=0.1.2` - that release fixes a real bug this pass caught upstream:
  `MqttSink.pub_online` was declared as a plain `def` instead of
  `async def` in `cync-lan`'s `protocols.py`, even though every real
  implementation (including this integration's own `CyncLanBridge`) is
  async. Harmless at runtime (Python doesn't enforce `Protocol`
  conformance dynamically) but was a real static-typing defect.

### 1.1.0

- Depend on the published `cync-lan` PyPI package instead of a vendored
  copy (`custom_components/cync_lan/vendor/`) - see `quality_scale.yaml`'s
  `dependency-transparency`/`async-dependency` entries. No functional
  change; this is the packaging fix those entries describe.

### 1.0.0

- First major version number. This is a versioning milestone, not a
  stability claim: nothing functional changed from 0.5.0, every
  `experimental_*` service is exactly as experimental as before, and
  entities disabled-by-default remain disabled-by-default. See
  `quality_scale.yaml` and this integration's own README for what's
  actually confirmed-working versus still unconfirmed.

### 0.5.0

- Add a per-device MITM-mode toggle switch (diagnostic category, disabled
  by default - most users never need it): puts a device into the
  underlying package's traffic-capture debug mode, useful for helping add
  support for new devices/features. Reads/writes the device's own TCP
  session directly rather than the underlying package's MQTT-discovery
  mechanism, which this integration's static entity model has no use for.
- Add two connection-diagnostic sensors, exactly one per device depending
  on how it connects: **IP address** for WiFi-capable devices (the LAN
  address of its own direct connection), or **Connected via** for
  BTLE-mesh-only devices (which WiFi-capable device is currently relaying
  its status - a new `CyncDevice.relay_source` tracking addition in the
  underlying package, set at every mesh status/MeshInfo parse site).

### 0.4.1

- Fix devices going unavailable when they lose power or network not being
  reflected in Home Assistant, showing stale last-known state indefinitely
  instead. The existing detection path (a mesh status broadcast reporting a
  device with a "not recently seen" flag) only catches this if *other*
  devices keep relaying broadcasts that still mention the affected one -
  it never covered the common case of a WiFi-connected plug/switch/bulb
  that owns its own direct TCP connection simply losing power, since that
  device's own dev_id just stops appearing in broadcasts entirely rather
  than appearing with a stale flag. The underlying package's code even
  already detected this exact scenario ("device probably dropped the
  connection (lost power)") but never acted on it - the TCP session
  closing now immediately marks that device offline.

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
