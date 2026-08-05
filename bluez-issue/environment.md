## Environment

* **BlueZ:** 5.86  (the `discover_descs()` single-descriptor optimization is
  present unchanged on current master, verified 2026-08)
* **Kernel:** 6.18.34-haos-raspi (aarch64), Home Assistant OS
* **Adapter:** Raspberry Pi 5 built-in — Broadcom **BCM4345C0**, firmware
  `003.001.025`, patch `brcm/BCM4345C0.raspberrypi,5-model-b.hcd`,
  HCI manufacturer 0x0131, HCI version 0x09 (5.0)
* **Device:** GE Cync bulb/switch, Telink TLSR825x SDK firmware.
  Two separate units reproduce; a third unit with an empty 28-byte cache file
  (never previously connected) reproduces identically.

## Device coverage

Both OUI families on this account carry the same GATT layout, so the defect is
not specific to one product line:

| | `F4:BC:DA` | `78:6D:EB` |
| :--- | :--- | :--- |
| char `...1911` | 0x0011, props 0x1a (read/write/**notify**) | identical |
| BlueZ reports at 0x0013 | `0x2902` | `0x2902` |
| device returns on READ 0x0013 | `"Status"` | `"Status"` |
| 0x0016 / 0x0019 / 0x001c | `0x2901` | `0x2901` |

The full descriptor set reads back `"Status"`, `"Command"`, `"OTA"`, `"Pair"` -
one `0x2901` per characteristic, and no CCCD anywhere on the service.
