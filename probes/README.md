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
    --mesh-name NAME --mesh-password PASS --target 1 --toggle
```

`--self-test` validates the shipped crypto against a literal transcription of
[`juanboro/cync2mqtt`](https://github.com/juanboro/cync2mqtt)'s `acync`
(Apache-2.0, descended from `google/python-dimond` and `python-tikteck`) — an
independent implementation that demonstrably drives real hardware. It passes:
`_aes_ecb_encrypt`, `generate_sk` and `key_encrypt` are byte-for-byte
identical. That is meaningful evidence about `ble_provision` itself, and it
costs nothing to re-run.

**Status: crypto verified, hardware path untested.** Nobody has yet pointed
this at a device. A clean run that changes nothing visible is the interesting
result, not a failure — it would mean the write was accepted and the command
did nothing.

### Credentials

Mesh name and password come from your own cloud export — the same values the
integration's `query_mesh_credentials` button surfaces. Anyone holding them
controls the mesh, so treat them like a password and keep them out of any log
you paste into an issue.

### Safety

Sending only: one `set_power`, plus reading notifications. It does not
provision, does not write mesh credentials, and does not touch device
settings, so nothing here can re-key or unpair a device.
