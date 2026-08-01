# Protocol and research documentation

This directory holds the reverse-engineering record: what Cync devices say on
the wire, how the protocol was worked out, and how confident each claim is.

**Looking for setup instructions?** Those live in the
[wiki](https://github.com/Proxy-alt/cync-lan/wiki) — DNS redirection,
installation, troubleshooting, tips, debugging setup and firmware versions.
They are there so anyone hitting a router-specific problem can fix the guide
directly. The material here stays in the repository instead, because it is
cited from source comments, mirrored across all three release branches by CI,
and makes claims that are worth reviewing in a pull request rather than
editing in place.

## Read the confidence markers

Everything here labels its claims. This convention matters more than it
looks — most of these opcodes have never been seen working on real hardware,
and a wrong one fails silently rather than erroring:

- **confirmed** — cited to an exact decompiled-app class and line, proven by a
  real packet capture, or already shipping in production.
- **plausible** — a reasonable inference from decompiled evidence, not
  directly proven.
- **not found / blocked** — explicitly flagged as absent, rather than guessed
  at.

A "plausible" opcode is a hypothesis someone has not yet tested. Treat it as
one.

## The protocol

| Document | What it covers |
|---|---|
| [`packet_structure.md`](packet_structure.md) | The TCP wire format — framing, headers, checksums. Start here; the rest assumes it. |
| [`mesh_opcodes.md`](mesh_opcodes.md) | The mesh command layer: every known opcode, its payload, and how confident we are. The largest and most-cited document here. |
| [`cync_automations.md`](cync_automations.md) | Hub-side scenes, schedules and motion-sensor schedules — the automations that live on Cync hardware rather than in Home Assistant. |
| [`ble_provisioning_protocol.md`](ble_provisioning_protocol.md) | BLE GATT pairing, session-key derivation and provisioning of factory-default devices. |

## Open questions

| Document | What it covers |
|---|---|
| [`hardware_verification.md`](hardware_verification.md) | Of 27 experimental commands, one is confirmed against real hardware. This is what still needs testing, ordered by what a single test would resolve. **The most useful thing to read if you own Cync devices and want to help.** |
| [`hub_envelope_ab_test.md`](hub_envelope_ab_test.md) | Whether hub commands carry a 7-byte routing prefix. Two envelopes, exact bytes for both, and a toggle to test them against real hardware. |
| [`cloud_independence_research.md`](cloud_independence_research.md) | Could Cync's servers be replaced entirely? What is authenticated where, and what still depends on the cloud. |

## Reference

| Document | What it covers |
|---|---|
| [`known_devices.md`](known_devices.md) | Which device types are known to work, and which are not. |
| [`debugging_sessions/`](debugging_sessions/) | Annotated packet captures from real sessions — the raw evidence several claims elsewhere are built on. |

## If you are adding to this

Cite claims to a class and line, or to a capture. Mark the confidence tier.
Prefer an explicit "not found" over a plausible-looking guess: a wrong opcode
that looks right costs more than a documented gap, because the failure mode is
a command that silently does nothing.

`docs/` is mirrored byte-for-byte across the three artifact repositories -
[`cync-lan-lib`](https://github.com/Proxy-alt/cync-lan-lib),
[`cync-lan-mqtt`](https://github.com/Proxy-alt/cync-lan-mqtt) and
[`cync-lan`](https://github.com/Proxy-alt/cync-lan) - with `cync-lan-lib`
canonical. CI fails the build when they drift, so edit it in `cync-lan-lib`
and copy to the others in the same change.
