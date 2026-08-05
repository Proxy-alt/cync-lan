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
