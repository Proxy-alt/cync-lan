# `probes/` — scripts that ask hardware a question

Everything else on this branch is static analysis: the decompiled tree, the
annotation patch, the extracted catalogue. None of it can tell you whether a
command actually does anything to a real device.

These do. Each script exists to settle one specific question that reading the
app could not, and each is written to be run once, read, and argued with —
not to become a dependency.

## `ble_control_probe.py`

**Question: can an already-provisioned Cync device be controlled over
Bluetooth, using the crypto `cync_lan` already ships?**

This is the load-bearing unknown behind the `cync_ble` idea — a second,
BLE-only integration that would need no DNS redirection and could therefore
be submitted to `home-assistant/core`. Everything else in that plan is
packaging; this is where the risk is.

`cync_lan.ble_provision` already implements the whole session handshake,
because provisioning needs the same one — `build_pairing_write`,
`derive_session_key`, `verify_pairing_response`, and the Telink AES quirk
underneath them. The only genuinely missing layer is per-command
`encrypt_packet` / `decrypt_packet`, which is what this script adds. If it
works, those two functions are what moves into the library.

```bash
python probes/ble_control_probe.py --self-test     # no hardware needed
python probes/ble_control_probe.py --scan
python probes/ble_control_probe.py --mac AA:BB:CC:DD:EE:FF \
    --mesh-name NAME --mesh-password PASS --listen 20
python probes/ble_control_probe.py --mac AA:BB:CC:DD:EE:FF \
    --mesh-name NAME --mesh-password PASS --target 1 --toggle
```

**Run `--listen` before `--toggle`.** It performs the entire session
handshake and decrypts real status packets while sending no control command,
so it proves the part that can actually be wrong - the session key and the
packet crypto - without changing the state of anything. Control is one write
away after that. This matters when the hardware is a wall switch driving a
real load and nobody is in the building.

### Where the credentials come from

**Not from `query_mesh_credentials`.** That button is a hub command, and hub
commands currently get no reply at all - see `docs/hub_envelope_ab_test.md`,
where both candidate envelopes were tried and neither produced a response. So
that route is closed until the hub family is understood.

It does not need to be open. The mesh credentials never came from the hub in
the first place - they come from the cloud export, and the integration already
writes them to disk. Read `cync_mesh.yaml` under the config directory
(`CYNC_CONFIG_DIR`, which on a Home Assistant install is
`.storage/cync-lan/config/`):

| probe argument | field in `cync_mesh.yaml` |
|---|---|
| `--mesh-name` | the home's **`mac`** — yes, the MAC, not the home's `name` |
| `--mesh-password` | the home's **`access_key`**, as a string |
| `--target` | the device's id key (`deviceID`'s last three digits) |
| `--mac` | any one device's `mac` — commands are relayed through the mesh |

The name/password mapping is not a guess: `acync` builds its session as
`network(meshmacs, mesh['mac'], str(mesh['access_key']))` against
`network(meshmacs, name, password)`, and `cloud_api.py:_parse_raw_export`
stores exactly `("access_key", "id", "mac")` per home. Both projects derive
the device id the same way, `int(str(deviceID)[-3:])`.

Diagnostics is the wrong place to look: it redacts `mesh_password`
deliberately, and the export file is not redacted because it is not meant to
leave the machine. Treat it accordingly.

Never use `--target 0`. That is the mesh broadcast address and would command
every device at once.

`--self-test` validates the shipped crypto against a literal transcription of
[`juanboro/cync2mqtt`](https://github.com/juanboro/cync2mqtt)'s `acync`
(Apache-2.0, descended from `google/python-dimond` and `python-tikteck`) — an
independent implementation that demonstrably drives real hardware. It passes:
`_aes_ecb_encrypt`, `generate_sk` and `key_encrypt` are byte-for-byte
identical. That is meaningful evidence about `ble_provision` itself, and it
costs nothing to re-run.

**Status: CONFIRMED ON HARDWARE, 2026-07-28.**

A `set_power` sent over BLE to a wired Cync switch changed its state, and
cync-lan reported that change over its own TCP connection. The command went
out over one transport and the confirmation came back over a completely
independent one, so this is not a result that can be faked.

| what the run established | |
|---|---|
| session handshake | mutual auth **VERIFIED** — the device proved it derived the same key |
| credential mapping | the home's `mac` is the mesh name, its `access_key` the password |
| `decrypt_packet` | inbound traffic decoded with vendor `0x0211` at bytes 8:9 and readable ASCII |
| `encrypt_packet` | a `0xD0` command was accepted and acted on |
| opcode `0xD0` | `set_power` works over BLE against a switch |

Two things it did **not** establish. Notifications could not be subscribed to
at all — this firmware declares `notify` on `...1911`, rejects the CCCD write
with `Unlikely Error`, and then drops the connection, which is what
`--no-notify` exists for. And only `set_power` was exercised; brightness,
temperature and RGB are unconfirmed over this transport.

Worth knowing for anyone building on this: none of it needed DNS redirection,
and none of it needed the hub command family that currently gets no reply.

### Credentials

See the table above for which field is which. They come from `cync_mesh.yaml`,
**not** from the `query_mesh_credentials` button — that button is a hub
command, and hub commands currently get no reply at all.

Anyone holding them controls the mesh, so treat them like a password and keep
them out of any log you paste into an issue.

### Safety

Sending only: one `set_power`, plus reading notifications where the firmware
allows it. It does not
provision, does not write mesh credentials, and does not touch device
settings, so nothing here can re-key or unpair a device.
