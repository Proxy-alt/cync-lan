>[!IMPORTANT]
> [DNS redirection REQUIRED](./docs/DNS.md) - this library controls Cync
> devices by impersonating the cloud server they phone home to. Without
> redirecting those hostnames at your DNS, nothing connects.

# cync-lan (core protocol library)

Core async protocol library for local LAN control of Cync / C by GE smart
devices: the device and session TCP state machine, the wire-format packet
codec, Cync cloud authentication and device export, and BLE GATT
provisioning.

**This package is a dependency, not an application.** No MQTT, no Docker,
no configuration of its own, and no CLI aside from `cync-lan-ble-provision`.
It does nothing until a consumer wires it up to a running event loop - see
[Consumers](#consumers).

>[!WARNING]
> **DO NOT** contact GE / Savant for troubleshooting while using this
> project. Open an issue
> [here](https://github.com/Proxy-alt/cync-lan/issues) instead.

>[!WARNING]
> It is **HIGHLY** recommended that you do **NOT** perform firmware upgrades
> on Cync devices after running cync-lan. It would be trivial - change one
> parameter in a constructor - for Savant to disable this method of local
> control. This caution is inherited from upstream and still stands.

## Repository layout

Three separately-versioned, separately-released artifacts share this one
repository, each on its own branch. **You are on `core`.**

| Artifact | Branch | What it is | Distributed via |
|---|---|---|---|
| `cync-lan` | **`core`** (here) | This library - protocol, sessions, cloud auth, BLE | [PyPI](https://pypi.org/project/cync-lan/) |
| `cync-lan-mqtt` | [`python`](https://github.com/Proxy-alt/cync-lan/tree/python) | Standalone Docker/MQTT daemon + HTTP device exporter | [PyPI](https://pypi.org/project/cync-lan-mqtt/) + Docker image |
| `cync_lan` custom_component | [`feature/ha-custom-component`](https://github.com/Proxy-alt/cync-lan/tree/feature/ha-custom-component) | Native Home Assistant integration (no MQTT) | GitHub Release / HACS |

Bumping this library does not require bumping either consumer, or vice
versa. [RELEASING.md](./RELEASING.md) covers how the three are versioned,
including the rule that decides releases from prereleases: a plain `X.Y.Z`
version cuts a full release, `X.Y.ZbN` cuts a prerelease, and anything else
fails the build.

`docs/` is mirrored byte-for-byte across all three branches, with **this
branch as the canonical copy** - CI fails if they drift, because they had
already silently drifted once before that check existed.

## Consumers

- [`cync-lan-mqtt`](https://github.com/Proxy-alt/cync-lan/tree/python) - the
  Docker/MQTT add-on: standalone daemon, MQTT-based Home Assistant
  discovery, HTTP device-list exporter.
- [`custom_components/cync_lan`](https://github.com/Proxy-alt/cync-lan/tree/feature/ha-custom-component/custom_components/cync_lan) -
  the native Home Assistant integration, which talks to this package's
  device and session objects directly.

See either of those for end-user installation and setup.

## Installing

```
pip install cync-lan
```

Add the `ble` extra for BLE GATT provisioning of factory-default devices
via `bleak`:

```
pip install "cync-lan[ble]"
```

Requires Python 3.12+. That floor is real, not cautious: `devices.py` uses a
PEP 701 nested-quote f-string, on top of `enum.StrEnum` and `datetime.UTC`.

## What's in the package

| Module | Responsibility |
|---|---|
| `server.py` | The TCP server devices connect to once DNS is redirected; session pool, MITM/proxy mode |
| `devices.py` | Device model, capability classification, and the mesh command surface |
| `packet/` | Wire-format encode/decode (`PacketBuilder`) |
| `cloud_api.py` | Cync cloud auth (email + emailed OTP) and device-list export |
| `ble_provision.py` | BLE GATT provisioning of factory-default devices; the `cync-lan-ble-provision` entry point (`ble` extra) |
| `metadata/` | Device-type tables - which model is a light, a switch, dimmable, has a motion sensor |
| `protocols.py` | Protocol-level constants and enums |
| `structs.py` | Shared dataclasses - `EntityState`, `GlobalObject`, and friends |

## Protocol documentation

The reverse-engineering notes in `docs/` are the most useful part of this
repository if you are doing your own protocol work:

- [`docs/packet_structure.md`](./docs/packet_structure.md) - the wire format.
- [`docs/mesh_opcodes.md`](./docs/mesh_opcodes.md) - mesh command opcodes.
  **Read the confidence markers before trusting one.** Several are derived
  from the decompiled Android app and have never been run against real
  hardware; exactly one (`set_indicator_led`) is hardware-confirmed.
- [`docs/hardware_verification.md`](./docs/hardware_verification.md) - what
  still needs verifying on real devices, in the order worth doing it.
- [`docs/known_devices.md`](./docs/known_devices.md) - device types and
  support status.
- [`docs/ble_provisioning_protocol.md`](./docs/ble_provisioning_protocol.md) -
  the BLE provisioning flow.

## Development

```
pip install -e ".[ble,dev]"
pytest tests/ -q
```

`scripts/cmd_code.py` drives every mesh command through a stubbed transport
and checks the computed `cmd_code` length field against the bytes actually
emitted - `calc` for one command, `audit` for all of them. It runs in CI,
because `cmd_code` is a length field rather than an identifier and a wrong
one is silently accepted by the hardware.

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
  protocol knowledge encoded here. Upstream stopped at `0.0.6b16`.
- **[@CodeNeedsCoffee](https://github.com/CodeNeedsCoffee)** - initial work
  on the Home Assistant App.

Full license texts for all of the above are reproduced in
[LICENSE-3RD-PARTY](./LICENSE-3RD-PARTY).

## License

MIT, same as the original - see [LICENSE](./LICENSE).
