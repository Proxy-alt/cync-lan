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

### Installing the integration

1. Install via [HACS](https://hacs.xyz/) (custom repository) or copy this
   `custom_components/cync_lan` directory into your Home Assistant
   `config/custom_components/` directory.
2. Restart Home Assistant.
3. Go to **Settings → Devices & Services → Add Integration**, search for
   "Cync LAN".
4. Enter your Cync account email and password.
5. If prompted, enter the one-time verification code emailed to your
   account.
6. Confirm the device count found on your account to finish setup.

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
- **Switch** - binary on/off switches and outlets/plugs (shown with the
  `outlet` device class where applicable).
- **Fan** - fan controller switches, with percentage and preset-speed control.
- **Binary sensor** - standalone motion/occupancy sensor accessories, and a
  secondary motion entity on light/switch models with a built-in occupancy
  sensor.

Not yet supported in this integration (present in some form in the
underlying package, not yet exposed as HA entities here): per-device
settings like motion sensor sensitivity/timeout, status LED ring
color/brightness, and OTA firmware update triggering - see
[Known limitations](#known-limitations).

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
- **No dynamic per-device settings yet.** Motion sensor sensitivity/timeout,
  status LED configuration, and similar device settings are not yet
  exposed as HA entities - the protocol bytes for these were reverse
  engineered but not yet confirmed against a live capture (see the parent
  project's `devices.py` for the specific TODO).
- **OTA firmware updates are entirely out of scope.** Firmware delivery
  happens over a direct BLE connection or the device's own internet
  connection (depending on device type), not through this integration's
  local TCP listener - there's nothing for Home Assistant to trigger or
  monitor here.

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
