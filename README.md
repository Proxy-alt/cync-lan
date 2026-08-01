<p align="center">
  <picture>
    <!-- The mark is black on transparent, so it is close to invisible on
         GitHub's dark theme without this. -->
    <source
      media="(prefers-color-scheme: dark)"
      srcset="custom_components/cync_lan/brand/dark_logo@2x.png">
    <img
      src="custom_components/cync_lan/brand/logo@2x.png"
      alt="Cync LAN"
      width="420">
  </picture>
</p>

>[!IMPORTANT]
> [DNS redirection REQUIRED](https://github.com/Proxy-alt/cync-lan/wiki/DNS) - this integration controls your
> devices by impersonating the cloud server they phone home to. Without
> redirecting those hostnames at your router, nothing connects.

# Cync LAN

**Local control of Cync / C by GE smart lighting and switches, as a native
Home Assistant integration.** No cloud round-trip at runtime, no MQTT
broker, no Docker.

Home Assistant runs a small TCP/TLS server that your Cync devices connect to
*instead of* Cync's real cloud servers, via a DNS override you configure at
your router. From then on every command and state update stays on your LAN.

>[!NOTE]
> **This top-level README is the overview.** For full setup, every
> configuration option, all supported entity types, services, example
> automations and troubleshooting, see
> **[`custom_components/cync_lan/README.md`](./custom_components/cync_lan/README.md)**.
> There are also two *other* ways to run this project - see
> [Repository layout](#repository-layout).

>[!WARNING]
> **DO NOT** contact GE / Savant for troubleshooting while using this
> project. Open an issue
> [here](https://github.com/Proxy-alt/cync-lan/issues) - this is a fork, so
> please don't send its bugs upstream to @baudneo.

>[!WARNING]
> It is **HIGHLY** recommended that you do **NOT** perform firmware upgrades
> on Cync devices after running cync-lan. It would be trivial - change one
> parameter in a constructor - for Savant to disable this method of local
> control. This caution is inherited from upstream and still stands.

**This is a work in progress and may not work for every device.** See
[`docs/known_devices.md`](docs/known_devices.md). Most battery-powered
devices remain unsupported because cync-lan can only listen to them, not
write to them; the standalone motion sensor and "Wireless Switch"
accessories are the exception, both exposed as `occupancy` binary sensors.

## Prerequisites

1. **[DNS redirection](https://github.com/Proxy-alt/cync-lan/wiki/DNS)** of the `xlink.cn`-family hostnames
   to your Home Assistant host, at your router or a local DNS server
   (Pi-hole, AdGuard Home, dnsmasq). Devices already talking to the real
   cloud need a **power cycle** to pick up the change.
2. At least one **mains-powered Wi-Fi ("Direct Connect") Cync device** to
   act as the TCP↔Bluetooth bridge for the rest of the mesh. Battery
   devices cannot do this.
3. A free port on the Home Assistant host for the listener - `23779` by
   default, configurable during setup.
4. Your **Cync account email and password**, plus access to that inbox
   (Cync emails a one-time code during setup).
5. **Home Assistant 2024.11.0+**. That floor is load-bearing, not cautious:
   it is where `ConfigFlow._get_reauth_entry()` and
   `async_update_reload_and_abort`'s `data_updates` argument landed, both
   required for reauthentication here.

>[!NOTE]
> You still need the official Cync app to add new devices to your account as
> you acquire them. This integration controls devices that are already on
> the account - it does not replace onboarding. (The core library's
> `cync-lan-ble-provision` CLI is an experimental exception.)

## Installation

Install through HACS as a custom repository, or copy the component in by
hand. Full instructions are in the
[integration's own README](./custom_components/cync_lan/README.md#installing-the-integration).

Once installed and Home Assistant is restarted, go to **Settings → Devices &
Services → Add Integration → "Cync LAN"**, and enter your account details.
Home Assistant may also surface a discovered "Cync LAN" card on its own -
Cync devices take DHCP leases with a recognisable `GE_*` hostname pattern.

## What you get

Native Home Assistant entities - no MQTT anywhere in the path:

- **Light** - on/off, brightness, color temperature, RGB and effects,
  according to what each model actually supports.
- **Light (group)** - one aggregate entity per Cync group/room, opt-in.
- **Switch** - binary switches, outlets and plugs.
- **Fan** - fan-controller switches, with percentage and preset speeds.
- **Binary sensor** - standalone motion/occupancy accessories, plus a
  secondary motion entity on models with a built-in sensor.
- **Select / Number / Switch (config)** - indicator-LED mode, color,
  brightness and blink-on-disconnect. Confirmed on real hardware.
- **Sensor (diagnostic)** - per-device connection and identity diagnostics,
  plus native Cync motion-schedule slots where configured.
- **Scene / Button** - Cync scenes and schedules as real entities, and
  buttons for identify, hub queries and cleanup actions.

A number of the more speculative capabilities are gated behind an
**experimental features** toggle and are off by default. That is a real
warning rather than a formality: most mesh opcodes here were derived from
the decompiled Android app and have never been confirmed against hardware.
See [Known limitations](./custom_components/cync_lan/README.md#known-limitations).

## Repository layout

Three separately-versioned, separately-released artifacts, each in its own
repository. **You are in the Home Assistant integration.**

| Artifact | Repository | What it is | Distributed via |
|---|---|---|---|
| `cync-lan` | [`cync-lan-lib`](https://github.com/Proxy-alt/cync-lan-lib) | Core protocol library - sessions, packet codec, cloud auth, BLE | [PyPI](https://pypi.org/project/cync-lan/) |
| `cync-lan-mqtt` | [`cync-lan-mqtt`](https://github.com/Proxy-alt/cync-lan-mqtt) | Standalone Docker/MQTT daemon + HTTP device exporter | [PyPI](https://pypi.org/project/cync-lan-mqtt/) + [ghcr.io](https://github.com/Proxy-alt/cync-lan-mqtt/pkgs/container/cync-lan-mqtt) image |
| `cync_lan` custom_component | **`cync-lan`** (here) | This: native Home Assistant integration | GitHub Release / HACS |

They used to share this repository, one branch each. That broke HACS, which
resolves an integration's version from the *newest release in the list*
rather than from the Latest flag, and so kept offering `cync-lan-v*` tags -
which carry no `custom_components/` directory - as this integration's
update. [RELEASING.md](./RELEASING.md) has the full explanation.

### Which one do you want?

All three need the same [DNS redirection](https://github.com/Proxy-alt/cync-lan/wiki/DNS); they differ in
everything else.

| | This integration (HACS) | Home Assistant App ([hass-addons](https://github.com/Proxy-alt/hass-addons)) | Docker Compose ([`cync-lan-mqtt`](https://github.com/Proxy-alt/cync-lan-mqtt)) |
|---|---|---|---|
| Requires Docker | No | Yes, Supervisor manages it | Yes, you run it |
| Requires an MQTT broker | No | Yes | Yes |
| Configuration | HA config flow - no YAML | Supervisor Options UI | Environment variables |
| Devices appear as | Native HA entities | MQTT-discovered entities | MQTT-discovered entities |
| Token encryption key | Automatic | You set `secret_key` | You set `CYNC_SECRET_KEY` |

This integration is the newest and least Docker-dependent option, and it
installs through HACS like any other custom repository. The App is the least
fiddly version of the well-established Docker/MQTT path. Plain Docker Compose
gives the most direct control.

The three are versioned independently. [RELEASING.md](./RELEASING.md) covers
the details, including the rule that decides releases from prereleases: a
plain `X.Y.Z` version cuts a full release, `X.Y.ZbN` cuts a prerelease,
and anything else fails the build.

`docs/` is mirrored byte-for-byte across all three repositories (canonical
copy in [`cync-lan-lib`](https://github.com/Proxy-alt/cync-lan-lib)), so any
`docs/` link resolves in any of them. CI enforces it.

## About this fork

This repository is a fork of
[baudneo/cync-lan](https://github.com/baudneo/cync-lan), which did the
substantial async rewrite this continues from. Upstream stopped receiving
updates at `0.0.6b16`; everything from `0.0.6b17` onward exists only here,
including:

- **This Home Assistant integration**, which does not exist upstream at all.
  Not an add-on or an MQTT bridge - a real `custom_component` with its own
  config flow, entities and services.
- **Real motion-sensor support**: the standalone accessory and the
  battery-powered "Wireless Switch" both appear as `occupancy` binary
  sensors, and models with a built-in sensor get a second entity for it.
- **54 previously-unrecognised device types**, plus corrected
  classification (light vs switch, dimmable vs not) for several wired
  switch types that were simply wrong.
- **Real data-loss and crash fixes** found via a new unsupported-device
  debug capture: silently dropped status updates on certain packet
  variants, a TCP framing bug that discarded an entire read on one
  misaligned byte, and a crash that could permanently kill MQTT.
- **The protocol code split into a reusable
  [`cync-lan`](https://pypi.org/project/cync-lan/) library**, so all
  consumers share one implementation instead of vendoring copies that drift.
- **Substantially expanded protocol documentation** in
  [`docs/mesh_opcodes.md`](docs/mesh_opcodes.md), reverse-engineered from
  the decompiled Android app, with explicit confidence markers.
- **Test suites and CI where there were none**, across all three branches.

See [`custom_components/cync_lan/CHANGELOG.md`](./custom_components/cync_lan/CHANGELOG.md)
for this integration's history and [CHANGELOG.md](./CHANGELOG.md) for the
project's.

## Documentation

**Setup guides live in the [wiki](https://github.com/Proxy-alt/cync-lan/wiki)** -
[DNS redirection](https://github.com/Proxy-alt/cync-lan/wiki/DNS) (the one hard
requirement), [installation](https://github.com/Proxy-alt/cync-lan/wiki/Installation),
[Troubleshooting](https://github.com/Proxy-alt/cync-lan/wiki/Troubleshooting),
[Tips](https://github.com/Proxy-alt/cync-lan/wiki/Tips),
[debugging setup](https://github.com/Proxy-alt/cync-lan/wiki/Debugging-Setup) and
[firmware versions](https://github.com/Proxy-alt/cync-lan/wiki/Firmware-Versions).
They are there rather than here so you can fix them yourself - router UIs change
and nobody owns every model.

Protocol research stays in the repository, because it is cited from source
comments, mirrored across all three branches by CI, and its claims carry
confidence markers worth reviewing in a pull request:

- [`docs/known_devices.md`](docs/known_devices.md) - device types and
  support status.
- [`docs/packet_structure.md`](docs/packet_structure.md) - the wire format.
- [`docs/mesh_opcodes.md`](docs/mesh_opcodes.md) - mesh command opcodes.
  **Read the confidence markers before trusting one.**
- [`docs/hardware_verification.md`](docs/hardware_verification.md) - what
  still needs testing on real devices, in the order worth doing it.
- [`docs/cync_automations.md`](docs/cync_automations.md) - native Cync
  schedules and automations.
- [`docs/ble_provisioning_protocol.md`](docs/ble_provisioning_protocol.md) -
  BLE provisioning of factory-default devices.

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
  protocol knowledge here.
- **[@CodeNeedsCoffee](https://github.com/CodeNeedsCoffee)** - initial work
  on the Home Assistant App.

Full license texts for all of the above are reproduced in
[LICENSE-3RD-PARTY](./LICENSE-3RD-PARTY).

## License

MIT, same as the original - see [LICENSE](./LICENSE).
