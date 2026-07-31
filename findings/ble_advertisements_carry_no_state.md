# Advertisements carry identity, not state

**Status: settled on hardware, negative.** Cync devices advertise Telink
vendor data constantly, but its content does not change when a device's state
changes. Reading state passively - no connection, no connection slot, no CCCD
- is not possible on this hardware.

## Why this was worth testing

Every approach this project uses reads state through a *connection*: connect,
authenticate, write the vendor enable byte, and catch the notification sweep
in the seconds before the firmware kills the link. That works, and the decode
is confirmed, but it costs a connection slot on a radio Home Assistant shares
with every other Bluetooth integration - and connection establishment, not the
protocol, has been the source of every reliability problem in the HA
integration.

If any state rode in the advertising data, none of that would be needed. It
would work on any adapter, through any ESPHome proxy, with no slots and no
contention, and would be `local_push` for free - the shape Home Assistant most
prefers. Cheap to test, and decisive either way.

## Method

Capture every distinct Telink (company ID **529 = 0x0211**, the same vendor ID
that prefixes the TCP payload and mesh packets) manufacturer-data payload per
device over an 18s window; drive one device on and off over BLE between
captures; compare the payload sets.

## Result

44 devices advertised Telink data in every phase. Payload **content never
differed**. Two devices showed a differing payload *set*, and both are
sampling artifacts rather than content changes - the long payload simply was
not observed during one window:

```
78:6D:EB:08:FE:79
  baseline  ['110279fe08eb440001a100786deb0e7e79060708090a0b0c0d0e0f', '112279fe08eb']
  ON        ['110279fe08eb440001a100786deb0e7e79060708090a0b0c0d0e0f', '112279fe08eb']
  OFF       ['112279fe08eb']
```

Identical strings in baseline and ON; OFF is a subset. Neither of the two was
the device actually being driven.

## What the payloads contain

Two shapes, both static:

```
short: 1102 d40339da
       ^^^^ ^^^^^^^^
       vendor  first 4 bytes of the address, reversed

long:  1102 d40339da 35 0001 2b00 8850f60426fe 060708090a0b0c0d0e0f
                                  ^^^^^^                ^^^^^^^^^^^^^^^^^^^^
                                  mesh/home reference   sequential filler
```

The `8850f6...` field matches the *other* home's mesh identifier present in
this account's export, so the long form looks like an identity/provisioning
beacon naming the mesh a node belongs to. The trailing `060708090a0b0c0d0e0f`
is plainly filler.

Device families differ in which they emit: the `F4:BC:DA` majority mostly
advertise the short identity form only, while `78:6D:EB` / `30:C0:1B` /
`34:13:43` nodes also emit the long form. Neither carries a field that tracks
power or brightness.

## Methodology note

The first attempt fingerprinted the *whole* advertisement and reported ten
devices "changing", which was wrong. Company ID **14798 (0x39CE)** emits a
fresh 27-byte random-looking payload on essentially every packet, so any
comparison including it always differs. Whatever that is - rolling identifier,
encrypted beacon - it swamps a naive diff. Restricting the comparison to the
Telink company ID is what made the answer legible.

## Consequence

A connection remains the only way to read state, so the harvest stays as it
is. This closes the last approach that needed no new hardware; what remains
untested is the **ESPHome Bluetooth proxy**, which does not avoid the
connection but does supply its own GATT client and its own connection slots,
uncontended by whatever else shares the local adapter.
