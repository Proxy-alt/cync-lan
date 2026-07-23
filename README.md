# cync-lan

Core async protocol library for local LAN control of Cync/C by GE smart
devices - the device/session TCP state machine, wire-format packet codec,
Cync cloud auth, and BLE GATT provisioning. No MQTT, no Docker, no CLI of
its own (aside from `cync-lan-ble-provision`, see below) - this package is
a dependency consumed by other projects, not something you run directly.

Requires [DNS redirection](https://github.com/Proxy-alt/cync-lan/blob/feature/ha-custom-component/docs/DNS.md)
of Cync's cloud hostnames to wherever this package's TCP server (`server.py`)
runs, same as every consumer of this package.

## Consumers

- [`cync-lan-mqtt`](https://github.com/Proxy-alt/cync-lan/tree/python) - the
  Docker/MQTT add-on (standalone daemon, MQTT-based Home Assistant
  discovery, HTTP device-list exporter).
- [`custom_components/cync_lan`](https://github.com/Proxy-alt/cync-lan/tree/feature/ha-custom-component/custom_components/cync_lan) -
  the native Home Assistant integration (talks to this package's device/
  session objects directly, no MQTT).

See either of those repos/branches for end-user installation and setup -
this package by itself doesn't do anything without a consumer wiring it up
to a running event loop.

## Installing

```
pip install cync-lan
```

Add the `ble` extra (`pip install "cync-lan[ble]"`) for BLE GATT
provisioning of factory-default devices via `bleak`.

## License

MIT - see `LICENSE`.
