<p align="center">
  <picture>
    <!-- The mark is black on transparent, so it is close to invisible on
         GitHub's dark theme without this. -->
    <source media="(prefers-color-scheme: dark)" srcset="brand/dark_logo@2x.png">
    <img src="brand/logo@2x.png" alt="Cync LAN" width="420">
  </picture>
</p>

# Cync LAN (Home Assistant custom_component)

## What this is

Cync LAN is a local integration for Cync (formerly "C by GE") smart lighting
and switch devices. It runs a small TCP/TLS server inside Home Assistant
that your Cync devices connect to instead of Cync's real cloud servers (via
a DNS override you configure at your router - see [Prerequisites](#prerequisites)
below). Once connected, all device control and state updates happen entirely
on your local network - no cloud round-trip at runtime.

This integration is a thin Home Assistant adapter around the
[`cync-lan`](https://github.com/Proxy-alt/cync-lan) Python package, which
does the actual protocol work (see [Known limitations](#known-limitations)
for what that dependency relationship means in practice).

See [CHANGELOG.md](./CHANGELOG.md) for this integration's own version
history (independent of the underlying `cync-lan` package's own versioning
- see that project's root `CHANGELOG.md`).

## Installation

### Prerequisites

1. **DNS redirection.** Cync devices are hardcoded to look up `xlink.cn`-family
   domains. You must redirect that DNS query, at your router or a local DNS
   server (e.g. Pi-hole, AdGuard Home, dnsmasq), to the IP address of the
   machine running Home Assistant. Devices that were already connected to
   the real cloud need a reboot (power cycle) to pick up the new DNS
   resolution.
2. A port open on your Home Assistant host for the local listener (`23779`
   by default, configurable during setup) - the Cync devices connect to
   this over your LAN.
3. Your Cync account email and password, and access to that account's email
   inbox (Cync emails a one-time verification code during setup).
4. **Home Assistant 2024.11.0 or newer**, and Python 3.12+ (which any
   supported Home Assistant install already provides). 2024.11 is where
   `ConfigFlow._get_reauth_entry()` and `async_update_reload_and_abort`'s
   `data_updates` argument landed - both load-bearing for reauthentication
   here - and the floor was verified by checking those APIs against real
   2024.10 and 2024.11 installs, not inferred. `hacs.json` declares the same
   minimum, so HACS will not offer the integration to older instances.
   Continuous integration runs against the current release (2026.7).

### Installing the integration

#### Via HACS (recommended)

1. In HACS, go to **Integrations → ⋮ (top right) → Custom repositories**.
2. Add `https://github.com/Proxy-alt/cync-lan`, category **Integration**.
3. Find "Cync LAN" in HACS and install it.
4. Restart Home Assistant.

#### Manual installation

1. Copy this `custom_components/cync_lan` directory into your Home
   Assistant `config/custom_components/` directory.
2. Restart Home Assistant.

#### Finishing setup

Home Assistant may prompt you to set this integration up on its own -
Cync devices get a DHCP lease with a recognizable hostname pattern
(`GE_*`), so **Settings → Devices & Services** may already show a
"Cync LAN" discovered card once a device shows up on your network. If
not (or you'd rather start it yourself):

1. Go to **Settings → Devices & Services → Add Integration**, search for
   "Cync LAN".
2. Enter your Cync account email and password.
3. If prompted, enter the one-time verification code emailed to your
   account.
6. Confirm the device count found on your account to finish setup.

Either way leads to the same account-credentials form - discovery just
saves you from finding the integration yourself, it doesn't skip entering
your Cync account details.

>[!NOTE]
> If Home Assistant instead shows a "Factory-default Cync device found"
> card (triggered by Bluetooth, not DHCP), that's a *different* signal: it
> means a brand-new device nearby hasn't been added to any Cync account
> yet. This integration only controls devices already on your account -
> add it via the official Cync app first (or see the root repository's
> `cync-lan-ble-provision` CLI tool to skip the Cync app entirely), then
> come back here.

### Removing the integration

**Settings → Devices & Services → Cync LAN → ⋮ → Delete.** This stops the
local listener and removes all devices/entities it created. It does **not**
undo your router's DNS override - remove that separately if you want your
devices to reconnect to Cync's real cloud servers afterward (they'll need a
power cycle to pick up the DNS change either direction).

## Configuration parameters

Set during initial setup, changeable afterward via **Settings → Devices &
Services → Cync LAN → Configure**:

| Parameter | Description | Default |
|---|---|---|
| Local TCP port | The port the listener binds to. Must match whatever you redirect Cync's DNS traffic to. | `23779` |
| Export refresh interval (hours) | How often to silently re-check your Cync account for newly added devices and reload if the device list changed. `0` disables automatic refresh. | `24` |
| Create light group entities | Expose each Cync device group ("Living Room", etc.) as an aggregate `light` entity, built on Home Assistant's own group-light implementation. | Off |
| Hide group members | When light groups are enabled, hide each group's individual member lights from dashboards (they still exist and still work, just out of the way). | Off |

Account email/password are collected once during setup and are not exposed
as an ongoing configuration parameter - use the reauthentication flow
(triggered automatically if the cached cloud session can't be refreshed) to
update them.

## How data updates

This integration is **local push**, not polling. Cync devices maintain a
persistent TCP connection to the local listener and report state changes
(power, brightness, color, motion, etc.) as they happen - typically within
a second of a physical switch press or app command. There is no polling
interval for device state itself.

The one thing that *is* periodic is the device *list* (not device state):
on the interval configured above, the integration silently re-pulls your
Cync account's device list to catch newly added devices, and reloads itself
if anything changed. This is separate from, and much less frequent than,
real-time state updates.

## Supported devices and functions

Device support is inherited entirely from the `cync-lan` dependency's
device-type table, built from real packet captures rather than an official
spec (Cync has never published one). See
[`docs/known_devices.md`](https://github.com/Proxy-alt/cync-lan/blob/python/docs/known_devices.md)
in that repository for the current list of confirmed device types and their
capabilities.

Supported entity types in this integration specifically:

- **Light** - on/off, brightness, color temperature, RGB color, and effects,
  depending on what the specific device model supports.
- **Light (group)** - one aggregate entity per Cync device group ("Living
  Room", etc.), opt-in via the "Create light group entities" configuration
  parameter above. Reads as on if any member is on, and mirrors Home
  Assistant's own built-in group-light behavior for brightness/color
  averaging. Adding or removing a device from a group's own Cync-side
  membership is available via the `cync_lan.experimental_set_group_membership`
  service; setting a whole group's power in one command (without going
  through HA's per-member group behavior) is
  `cync_lan.experimental_set_group_power`. Both are EXPERIMENTAL - see
  [Known limitations](#known-limitations).
- **Switch** - binary on/off switches and outlets/plugs (shown with the
  `outlet` device class where applicable).
- **Fan** - fan controller switches, with percentage and preset-speed control.
- **Binary sensor** - standalone motion/occupancy sensor accessories, and a
  secondary motion entity on light/switch models with a built-in occupancy
  sensor.
- **Select, Number, Switch (config)** - indicator-LED mode, color,
  brightness, and blink-on-WiFi-disconnect, one config entity per device.
  Confirmed working on real hardware. These are write-only (the device
  never reports its indicator LED back), so they're `assumed_state`
  entities that restore their last-known value across HA restarts rather
  than reading live device state.
- **Sensor (diagnostic)** - one entity per native Cync motion-sensor
  schedule slot (morning/daytime/evening/sleep), for devices that belong to
  a Cync app group with schedules configured. Read-only. Every device also
  gets exactly one of two connection-diagnostic sensors: **IP address**
  (WiFi-capable devices - the LAN address of its own direct TCP
  connection) or **Connected via** (BTLE-mesh-only devices - which
  WiFi-capable device is currently relaying its status).

  Every device additionally gets, in the **Diagnostic** section of its
  device page:

  | Entity | What it tells you |
  |---|---|
  | **Last seen** | When the device last reported anything. Answers "offline *since when*" - and unlike every other entity it deliberately stays readable after the device goes unavailable, which is exactly when you need it. |
  | **Cync device ID** | The numeric mesh ID, which appears in debug logs and in every raw `experimental_*` action. Disabled by default - it never changes, so it is there for filing a bug report rather than for a dashboard. |

  Every device also gets an **Identify** button, which makes it announce
  itself so you can tell which physical device an entity is. It is not gated
  behind experimental commands - it is non-destructive and self-limiting.

  Dimmer switches additionally get **Dimmer LED bar** and **Dimmer LED
  brightness** (experimental) for the row of level LEDs, which is separate
  from the small status LED the indicator-LED entities drive.

  With experimental commands enabled, the bridge also gains **Hub firmware
  version** and **Hub clock** (both disabled by default - each poll sends a
  real command to the hub). The clock is the useful one: native Cync
  Schedules fire off the hub's own clock rather than Home Assistant's, so
  drift there silently shifts when your Cync-side automations run.

  The **Cync LAN Bridge** device also gains **Connected devices**, the number
  of devices currently holding a connection to the local listener. Zero is
  the signature of the DNS redirection not being in place - the most common
  setup failure, and otherwise only surfaced by a repair notice that waits
  ten minutes before appearing.
- **Switch (diagnostic, disabled by default)** - a MITM-mode toggle per
  WiFi-capable device, for capturing traffic to help add support for new
  devices/features - see [Known limitations](#known-limitations). While
  active, the device disconnects from this integration and reconnects
  through the real Cync cloud instead, so it can't be controlled locally.
  Hidden by default since most users never need it; enable it in the
  entity registry if you do.
- **Scene** - one activatable entity per saved Cync Scene ("Routines ->
  Scenes"), reachable from HA's own scene picker instead of a raw service
  call. EXPERIMENTAL - see [Known limitations](#known-limitations).
- **Switch (schedule)** - one entity per saved Cync Schedule to
  enable/disable it without deleting it. Write-only/`assumed_state` (no
  live readback exists), seeded from your account's exported schedule
  data and restored across HA restarts. EXPERIMENTAL - see
  [Known limitations](#known-limitations).

Beyond entities backed by your existing account data, this integration can
also create new Scenes/Schedules directly on the Cync hub - see
[Pushing an HA automation to Cync hardware](#pushing-an-ha-automation-to-cync-hardware)
below.

Motion-sensor settings tuning also has a guided options-flow wizard
("Configure" -> "Edit motion sensor settings") that walks through waking
the physical sensor first, rather than requiring the raw service alone.

#### Sleeping sensors are refused, not silently dropped

Battery devices sleep, and a write aimed at a sleeping one never arrives.
Every path that writes motion-sensor settings or schedules - both
`experimental_*` actions and both wizards - checks the device is awake
first, and refuses with an error telling you to hold its off button for
five seconds until the LED turns green.

That check is the device's ordinary online status, which is exactly what
the real Cync app uses: its wake-up screen watches the same availability
signal every device type reports, and there is no separate "discoverable"
state to detect. The app, however, *sends anyway* when the target is
asleep and reports success without transmitting. Refusing is the one
place this integration deliberately behaves better than the app rather
than matching it - a silent no-op here is indistinguishable from a wrong
opcode, and would send you debugging the wrong thing.

Not yet supported in this integration (present in some form in the
underlying package, not yet exposed as HA entities here): motion/ambient
sensor sensitivity and timing tuning as an entity (service + guided wizard
only, not a bare entity), and OTA firmware update triggering - see
[Known limitations](#known-limitations).

### Experimental features are opt-in

None of the `experimental_*` actions exist until you turn them on:

**Settings -> Devices & services -> Cync LAN -> Configure -> General
settings -> "Enable experimental commands (advanced)"**

They are off by default because every one of them sends a mesh command whose
outer envelope byte is *predicted* from a length formula rather than
confirmed against a real packet capture, and most have never been exercised
against real hardware. Turning the option on registers the actions and adds
the buttons below; turning it back off removes them again.

#### If hub commands do nothing: the envelope experiment

With experimental commands on, the same screen gains **"Use the alternate
'bare' hub envelope (experiment)"**.

Hub commands - scenes, schedules, automations, groups, hub queries - are sent
with a 7-byte block that addresses a mesh device. The decompiled Cync app
does not include that block on its own hub commands, which is a reason to
suspect ours, though not proof: the app talks phone-to-device, while this
integration sits between device and cloud.

So both shapes are available. If hub commands appear to do nothing with the
toggle off, turn it on and try the same thing again. Either result is worth
reporting - "it only worked with it on" and "it worked with it off" are both
answers, and the question cannot be settled from the decompiled app alone.

It applies immediately, with no reload or restart, so trying both is cheap.
The default is exactly what earlier versions sent, and it needs `cync-lan`
0.5.0 or newer (pinned in the manifest); with an older library the toggle
cannot take effect and the log says so rather than leaving you to read a
silent no-op as a result.

#### Everything is reachable without Developer Tools

With the option on, every experimental command has a UI route. Commands that
have persistent state become entities; the rest are guided forms under
**Configure -> Experimental commands**, because they take several parameters
and have nothing to read back.

| Command | Where it lives |
|---|---|
| Indicator LED mode / colour / brightness / wifi blink | Entities on each device |
| Activate a scene | Scene entities |
| Enable/disable a schedule | Switch per schedule |
| Group power | Switch per group |
| MultiColor gradient mode | Switch per colour-capable device |
| MultiColor segment count | Number per colour-capable device |
| Motion-sensor sensitivity/timing | Configure -> Edit motion sensor settings |
| Query mesh credentials | Button on the bridge |
| Delete a scene / schedule | Button per scene / schedule |
| Push an HA automation to the hub | Configure -> Experimental commands |
| Add/remove a device in a scene | Configure -> Experimental commands |
| Add/remove a device in a group | Configure -> Experimental commands |
| Write a motion-sensor schedule slot | Configure -> Experimental commands |
| Program MultiColor segments | Configure -> Experimental commands |

The `experimental_*` actions all still exist for automations and scripts -
the UI routes are additions, not replacements.

#### Buttons (easier than the raw actions)

With the option on, the **Cync LAN Bridge** device gains buttons that carry
the ID they act on, so you never have to look up a numeric `scene_id` or
`schedule_id`:

| Button | What it does |
|---|---|
| Query mesh credentials | Reads your BTLE mesh name and password off a connected hub and shows them in a notification. Pass them to `cync-lan-ble-provision` to add a new device to your *existing* mesh instead of only a factory-default one. |
| Delete scene *&lt;name&gt;* | Deletes that specific Cync scene. One per scene. |
| Delete schedule *&lt;name&gt;* | Deletes that specific Cync schedule. One per schedule. |

The two delete buttons are **disabled by default**: Home Assistant has no
"are you sure?" dialog for a button press, and recreating a deleted scene
means going back to the phone app. Enable the entity first if you want it -
that deliberate extra step is the confirmation.

The mesh password is your mesh's shared secret. It is shown in a
dismissible notification rather than written to the log; dismiss it when you
are done.

### Services

Every raw service is prefixed `experimental_` as a visible risk signal in
Developer Tools -> Actions and the automation/script action picker - see
each service's own description there for its specific transport-confidence
caveats. They appear only when the option above is enabled.

| Service | Purpose |
|---|---|
| `set_indicator_led` | Set a device's indicator LED mode, color, and brightness. **Not experimental** - confirmed working on real hardware, so it is available without the opt-in and is not `experimental_`-prefixed. The old `experimental_set_indicator_led` name still works. |
| `set_motion_sensor_settings` | Tune a motion/ambient-light sensor's sensitivity and timing. |
| `execute_scene` | Activate a saved Cync scene. |
| `set_group_power` | Turn a Cync device group on or off as one command. |
| `set_motion_sensor_schedule` | Write one slot of a device's native motion-sensor schedule. |
| `delete_scene` | Delete a saved Cync scene. |
| `query_mesh_credentials` | Return the BTLE mesh name and password as action response data. |
| `delete_schedule` | Delete a saved Cync schedule. |
| `toggle_automation` | Enable or disable a saved Cync schedule without deleting it. |
| `set_group_membership` | Add or remove one device from a Cync group's mesh address. |
| `push_automation_to_hardware` | Push an HA automation onto the hub as a native scene + schedule - see [above](#pushing-an-ha-automation-to-cync-hardware). |
| `add_device_to_scene` | Add/update one device's captured color within an existing scene - standalone version of what `push_automation_to_hardware` does internally. |
| `remove_device_from_scene` | Remove one device from an existing scene, without deleting the scene or affecting its other members. |
| `set_multicolor_gradient_mode` | Toggle gradient mode for a custom MultiColor light-strip scheme (segmented dynamic RGB, separate from factory effect presets). |
| `set_multicolor_segment_count` | Set the total logical segment count for a custom MultiColor scheme. |
| `set_multicolor_segments` | Set up to 2 segments' position/color in one call, for a custom MultiColor scheme. |

## Use cases

**Keep lighting control working when your internet is down.** Cync's own
app and official integration route every command through Cync's cloud -
if your ISP has an outage, your lights stop responding to voice assistants,
automations, and the app, even though the bulbs and your Wi-Fi network are
both fine. Because this integration replaces the cloud round-trip with a
direct local connection, lighting control keeps working through an internet
outage; only things that inherently need the internet (like the initial
account setup and syncing your device list) are affected.

**Faster automations.** A cloud round-trip adds real, noticeable latency to
"motion detected → turn on light"-style automations. Local push means state
changes and commands land in well under the round-trip time a cloud-based
integration needs.

**Reduce what leaves your network.** Every state change your Cync devices
make - lights turning on and off, motion being detected - normally gets
reported to Cync's servers whether or not you're using their app at that
moment. Once set up, this integration's runtime traffic never leaves your
LAN, for whichever devices you've pointed at it via DNS.

**Bridge Cync devices into a broader smart-home setup.** Once your Cync
lights and switches show up as normal `light`/`switch`/`fan`/`binary_sensor`
entities, they compose with anything else in Home Assistant - group them
into scenes with devices from other brands, use their motion sensors to
trigger climate control, expose them to voice assistants through HA's own
integrations rather than Cync's, etc.

## Example automations

Trigger a scene when a motion-capable switch detects activity:

```yaml
automation:
  - alias: "Turn on hallway light on motion"
    trigger:
      - platform: state
        entity_id: binary_sensor.hallway_switch_motion
        to: "on"
    action:
      - service: light.turn_on
        target:
          entity_id: light.hallway_switch
```

Notify if the bridge's local listener goes down (paired with the
diagnostic entities exposed by this integration):

```yaml
automation:
  - alias: "Cync LAN bridge unavailable"
    trigger:
      - platform: state
        entity_id: light.some_cync_light
        to: "unavailable"
        for: "00:05:00"
    action:
      - service: notify.mobile_app
        data:
          message: "Cync devices have been unreachable for 5 minutes"
```

## Pushing an HA automation to Cync hardware

`cync_lan.experimental_push_automation_to_hardware` reads an *existing* Home
Assistant automation and recreates it directly on the Cync hub as a native
Scene + Schedule, so the same trigger keeps firing even if Home Assistant or
your network goes down. The automation keeps running as a normal HA
automation afterward - this only *additionally* copies its logic onto the
hub.

Because a Cync Schedule only understands a fixed time-of-day and a
day-of-week filter, and a Cync Scene only understands a light's color (not
brightness, effects, or transitions), the automation being pushed must be
built narrowly:

- Exactly one trigger: a plain `time` trigger with a fixed `at:` time (no
  entity or template - Cync has no way to track a dynamic time source).
- At most one condition: a `time` condition with only `weekday:` set (no
  `before`/`after` range). Omit it entirely to run every day.
- One or more `light.turn_on` actions, each targeting a Cync LAN light
  directly (not a light group) with exactly one color set - `rgb_color` or
  `color_temp_kelvin`. Brightness, effects, and transitions are rejected
  outright rather than silently dropped, since a Cync Scene entry has no
  field for them.

```yaml
automation:
  - alias: "Porch light warm white at sunset-ish, weekdays"
    trigger:
      - platform: time
        at: "19:30:00"
    condition:
      - condition: time
        weekday:
          - mon
          - tue
          - wed
          - thu
          - fri
    action:
      - service: light.turn_on
        target:
          entity_id: light.porch
        data:
          color_temp_kelvin: 40
```

Call the service once against this automation's entity_id (Developer Tools
-> Actions, or from another automation/script) to push it. If the
automation's config changes afterward, call the service again to push the
updated version - it always creates a new Scene/Schedule pair on the hub
rather than editing the previous one in place, so old pushes should be
cleaned up with `cync_lan.experimental_delete_scene`/
`experimental_delete_schedule` if you no longer want them running natively.

## Known limitations

- **Single account per Home Assistant instance.** The underlying `cync-lan`
  package reads account credentials from process-wide configuration, not
  per-call arguments - it was built for one-account-per-container add-on
  deployment. This integration enforces one config entry at a time
  (`unique-config-entry`) rather than silently misbehaving with two.
- **DNS interception, not an official API.** This works by making your Cync
  devices believe your Home Assistant instance *is* Cync's cloud server
  (via DNS redirection and a self-signed certificate the device firmware
  doesn't validate). It depends on that firmware behavior continuing to
  work; a firmware update from Cync could break it. See the parent
  project's README for more detail on this tradeoff.
- **Motion/ambient sensor tuning isn't exposed as entities yet.** Sensitivity,
  delay, and deactivation timing are only available via the
  `cync_lan.experimental_set_motion_sensor_settings` service - the mesh
  command's outer envelope byte is a predicted value, not yet confirmed
  against a real hardware test (see the parent project's `devices.py` and
  `docs/mesh_opcodes.md`). Indicator-LED settings had the same status until
  this was fixed and confirmed working on real hardware - see
  `docs/mesh_opcodes.md`'s "Indicator LED ring" section - and are now real
  `select`/`number`/`switch` config entities.
- **OTA firmware updates are entirely out of scope.** Firmware delivery
  happens over a direct BLE connection or the device's own internet
  connection (depending on device type), not through this integration's
  local TCP listener - there's nothing for Home Assistant to trigger or
  monitor here.
- **Scene/Schedule entities are unvalidated against a real populated
  account.** The exact cloud-export field names they're parsed from were
  confirmed by decompiling the Cync app's own JSON deserialization code,
  not by cross-checking against a real account that actually has scenes
  or schedules configured (the one account available for this project's
  research has neither). If scene/schedule entities don't appear, or show
  the wrong scene/enabled state, please open an issue with (redacted)
  export data - see the parent project's `docs/cync_automations.md`.
- **Scene/Schedule creation's transport is unconfirmed.** Creating a Scene or
  Schedule (`experimental_push_automation_to_hardware`, or the underlying
  `create_scene`/`create_schedule` functions directly) waits up to 10
  seconds for the hub to report back an allocated ID over a notification
  channel with a different wire format than this integration's own confirmed
  command envelope. Whether that channel rides the same connection this
  integration intercepts, or is BLE-specific, hasn't been confirmed against
  real hardware yet - a timeout returns a clear error rather than silently
  hanging. See the parent project's `docs/cync_automations.md`.
- **Light groups are a virtual HA construct, not a Cync-side concept for
  most commands.** `experimental_set_group_power` targets a group's real
  Cync mesh address, but the light group *entity* itself is built entirely
  on Home Assistant's own group-light helper - turning it on/off issues one
  command per member device, not a single group-wide command.
- **MultiColor services are 3 raw wire primitives, not a full custom-scheme
  builder.** `set_multicolor_gradient_mode`/`set_multicolor_segment_count`/
  `set_multicolor_segments` are confirmed individually, but this integration
  doesn't know or replicate the order/timing the real Cync app uses across
  multiple calls when programming more than 2 segments - you'll need to
  experiment with call order yourself, and please report what you find.
- **MITM mode disconnects the device from local control while active.**
  It's a debugging aid for capturing traffic, not something to leave on -
  a device in MITM mode is proxied to the real Cync cloud and won't
  respond to any command from this integration until it's turned back
  off. Disabled by default (see [above](#supported-devices-and-functions))
  precisely so it can't be toggled on by accident.
- **"Connected via" reflects whichever device last relayed a status
  update, not a stable topology.** A BTLE-mesh-only device has no fixed
  "parent" - if the mesh reconfigures which WiFi-capable device relays
  it, this sensor's value changes accordingly. Not a bug, just not a
  permanent assignment.

## Troubleshooting

- **Setup fails with "could not bind to port".** Something else on your
  Home Assistant host is already using that port (commonly a leftover
  standalone `cync-lan` Docker add-on still running). Stop the other
  process, or pick a different port during setup and update your DNS/router
  redirection to match.
- **Entities show "Unavailable" after setup.** Confirm your DNS redirection
  is actually in effect (checking your router/Pi-hole logs for the Cync
  cloud domain resolving to your HA host's IP) and that the affected
  devices have been power-cycled since you set it up - Cync devices only
  do a fresh DNS lookup on boot.
- **Setup fails with "no devices found".** Your Cync account has no devices
  registered to it, or the wrong account was used. Double check in the
  official Cync app which account your devices are actually registered
  under.
- **Reauthentication keeps being requested.** The upstream package's cached
  cloud session expired and couldn't silently refresh - this is normal
  occasionally; just re-enter your password when prompted.
- **Reporting a bug about any `experimental_*` service.** Every experimental
  command/service invocation is automatically recorded (no setup needed) to
  a dedicated `experimental_features.log`, alongside your other cync-lan
  files in Home Assistant's own config directory - attach that file (not
  the full HA log) along with your device model when opening an issue.

Also attach a **diagnostics download** (**Settings → Devices & Services →
Cync LAN → ⋮ → Download diagnostics**). It reports versions, per-device
capability flags, per-session connection state and the relevant environment
- credentials and MAC addresses redacted - which is most of what a bug
report otherwise takes several rounds of questions to establish.

## Where this fits

This integration is one of three separately-versioned artifacts in the
[Proxy-alt/cync-lan](https://github.com/Proxy-alt/cync-lan) repository, each
on its own branch:

| Artifact | Branch | What it is |
|---|---|---|
| `cync-lan` | [`core`](https://github.com/Proxy-alt/cync-lan/tree/core) | The protocol library this integration depends on |
| `cync-lan-mqtt` | [`python`](https://github.com/Proxy-alt/cync-lan/tree/python) | Standalone Docker/MQTT daemon - an alternative to this integration |
| `cync_lan` custom_component | `feature/ha-custom-component` | **This** |

The repository's [root README](https://github.com/Proxy-alt/cync-lan/blob/feature/ha-custom-component/README.md)
compares the three and explains when you would want each.

## Credits

This project is the current link in a chain of earlier work, and none of it
would exist without the people below.

- **[iburistu](https://github.com/iburistu)** -
  [cync-lan](https://github.com/iburistu/cync-lan), the original. The first
  public demonstration that Cync devices could be controlled locally by
  impersonating the cloud server. MIT, © 2022 Zachary Linkletter.
- **[juanboro](https://github.com/juanboro)** -
  [cync2mqtt](https://github.com/juanboro/cync2mqtt), the original MQTT
  bridge and cloud-export approach. Apache-2.0. Little of that code survives
  verbatim at this point, but the attribution stays. Long live OSS.
- **[baudneo](https://github.com/baudneo)** -
  [baudneo/cync-lan](https://github.com/baudneo/cync-lan), the substantial
  async rewrite this fork continues from, and the origin of most of the
  protocol knowledge this integration relies on. This Home Assistant
  integration itself does not exist upstream, but the protocol work it
  stands on very much does.
- **[@CodeNeedsCoffee](https://github.com/CodeNeedsCoffee)** - initial work
  on the Home Assistant App.

Full license texts for all of the above are reproduced in
[LICENSE-3RD-PARTY](https://github.com/Proxy-alt/cync-lan/blob/feature/ha-custom-component/LICENSE-3RD-PARTY).

## License

MIT, same as the original - see
[LICENSE](https://github.com/Proxy-alt/cync-lan/blob/feature/ha-custom-component/LICENSE).
