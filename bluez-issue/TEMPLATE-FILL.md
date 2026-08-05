# Filling bluez/bluez's issue form

The repo uses a single template (`.github/ISSUE_TEMPLATE/issue.yml`) with seven fields. Copy each block into the matching box.

---

## ⚠ Why the attached trace is safe, and what would not have been

**A btsnoop is attached, and it is safe to publish** — but only because of how it was captured. Read this before substituting your own.

The template's trace field feeds [btsnoop-analyzer](https://github.com/Vudentz/btsnoop-analyzer), which sends the decoded trace to a third-party LLM API. Its default anonymization scrubs **MAC addresses and device names**. It does not scrub ATT payload bytes, and that is where the problem is:

```
ATT WRITE_REQ handle=0x001b value=0c a0a1a2a3a4a5a6a7 <8-byte proof>
```

- `a0…a7` is `R_app`, a fixed constant in the vendor's SDK - not a nonce.
- The 8-byte proof is derived from the mesh name and the mesh password.
- The mesh name is the home's MAC, which also appears as the BLE device name.
- The mesh password is the account's `access_key`, an integer the vendor SDK bounds to `0 … 999999999`.

With a known `R_app`, a known mesh name and a 10⁹ keyspace, that proof is brute-forceable offline in minutes. Publishing it hands over local control of every device on the mesh.

**The solution was to avoid it, not redact it.** The defect needs no authenticated session, so `btmon-startnotify.log` was captured with the vendor pairing never performed, and with `cync_ble` disabled so nothing else on the host could pair during the window. Verified: zero writes to `0x001b`.

If you ever re-capture, keep both of those conditions, and leave **"Skip anonymization" unchecked** either way.

---

## Field 1 — Description *(required)*

> Use the contents of `ISSUE.md`, **minus** its `## Environment` section (that
> goes in Field 7) and **minus** its `## Attachments` section (that goes in
> Field 6). Everything else — Summary, the attribute table, Control, Expected
> vs. actual, Notes on fault attribution, Possible directions — belongs here.

---

## Field 2 — To reproduce

```
1. Connect to the device and let gatt-client run discovery (observed via
   bleak/StartNotify, but the behaviour is in shared/gatt-client, not the
   binding).

2. In btmon, note that descriptor discovery issues FIND_INFORMATION_REQ for
   0x0004, 0x0016, 0x0019 and 0x001c - the descriptor slot following every
   OTHER characteristic - and never for 0x0013, the sole descriptor of the
   notify characteristic. The only slot skipped is the one following the only
   characteristic declaring notify.

3. ATT READ_REQ handle=0x0013 returns 53 74 61 74 75 73 ("Status") - an
   ordinary successful exchange. 0x0016/0x0019/0x001c return "Command", "OTA",
   "Pair", all declared 0x2901.

4. Call StartNotify. btmon shows WRITE_REQ handle=0x0013 value=0100. Outcome
   varies by attempt on the same device and firmware: either
   WRITE_NOT_PERMITTED, or no response at all followed ~30s later by teardown.
   Across 14 measured attempts in two runs, every one ended in UNLIKELY_ERROR
   after a consistent ~30s delay and the link was held for the full window in
   zero of them - while 17-19 notifications arrived per attempt.

5. Inspect /var/lib/bluetooth/<adapter>/cache/<MAC>: every descriptor BlueZ
   actually discovered is recorded 2901; the one it never asked about is
   recorded 2902.

6. bluetoothctl remove <MAC>, confirm the cache file is gone, reconnect.
   Identical result, and BlueZ writes 0013=00002902-... back into a freshly
   created cache having still issued no FIND_INFORMATION for it.
```

---

## Field 3 — btmon trace

**Attach `btmon-startnotify.log`.** It is safe to publish.

The credential problem is avoided rather than redacted: the defect needs no mesh authentication, so this capture was taken with the vendor pairing never performed. Verified — **zero writes to `0x001b` appear in it**. `cync_ble` was disabled during the capture so no other client on the host could pair either.

What it contains, in one connection:

```
FIND_INFORMATION_REQ 0x0004 / 0x0016 / 0x0019 / 0x001c   ← 0x0013 never queried
READ_REQ  handle=0x0013  →  READ_RSP 53 74 61 74 75 73   ← "Status"
WRITE_REQ handle=0x0013  value=0100                      ← the synthesized CCCD
```

Trimmed to the HCI layer: 8,991 records, mgmt-channel scan reports dropped. Those were two thirds of the file and carried every neighbouring device's address while adding nothing diagnostic. Format validated by round-trip parse - btsnoop v1, datalink 2001.

**The `.log` extension matters.** GitHub's uploader rejects `.btsnoop` ("File type .btsnoop not supported"), and the template's own instructions say `btmon -w btmon.log`, so `.log` is what it expects. The contents are an ordinary btsnoop binary either way - the extension carries no meaning to the parser.

---

## Field 4 — Analysis focus

```
GATT discovery
```

(Only meaningful if a trace is attached. `Disconnection analysis` is the alternative if you want the teardown examined rather than the discovery gap — but the discovery gap is the defect.)

---

## Field 5 — Privacy checkboxes

```
[ ] Skip anonymization          ← leave UNCHECKED
[x] I understand this trace …   ← tick, since a trace is attached
```

Leave anonymization on regardless. The trace carries no pairing material, but the mesh name travels as the BLE device *name* in advertisements, and name scrubbing is exactly what the default covers.

---

## Field 6 — Other logs

> Attach these five files (all in `bluez-issue/`), and paste the note below
> into the box.

```
Decoded ATT exchanges, captured from the HCI monitor socket
(HCI_CHANNEL_MONITOR, the same feed btmon consumes) and rendered as text.
The mesh name and the pairing payloads are redacted - they are crypto inputs
to the device's session key and are not relevant to the defect. Handles,
opcodes, the CCCD write and every response are untouched.

  capture-A-startnotify-failing.txt
      The failing path. Two sessions: one where the vendor enable write is
      acknowledged and 20 notifications stream while the 0x0013 write goes
      unanswered; one where pairing completes without an enable write and no
      notifications arrive. Together they isolate what actually starts the
      reporting - it is not the CCCD write.

  capture-B-handle-0x0013-read.txt
      READ_REQ 1300 -> "Status". The attribute exists, is readable, and is
      correctly typed by the device.

  capture-C-after-cache-removal.txt
      Discovery after `bluetoothctl remove`, still skipping 0x0013.

  capture-D-raw-hci-no-cccd-works.txt
      Control: same device, same handles, driven over HCI_CHANNEL_USER with
      the application as its own ATT client. Zero writes to 0x0013, 21
      notifications, connection still up.

  cache-gatt-db.txt
      The regenerated cache entry with the synthesized 0013=00002902 line in
      context.

A btsnoop of the same reproduction is attached in the trace field above. It
was captured without performing the vendor pairing at all, so it carries no
key material - the defect does not require an authenticated session.
```

---

## Field 7 — Versions

```
- BlueZ version: 5.86 (the discover_descs() single-descriptor optimization is
  present unchanged on current master, verified 2026-08)
- Kernel version: 6.18.34-haos-raspi (aarch64), Home Assistant OS
- Problematic device: GE Cync bulb/switch, Telink TLSR825x SDK firmware.
  Three units reproduce across two OUI families (F4:BC:DA and 78:6D:EB), which
  carry an identical attribute table. One of the three had never been connected
  before - a 28-byte cache file containing only Name= - and reproduced
  identically, which rules out stale cached state.
  Adapter: Raspberry Pi 5 built-in, Broadcom BCM4345C0, firmware 003.001.025,
  patch brcm/BCM4345C0.raspberrypi,5-model-b.hcd, HCI manufacturer 0x0131,
  HCI version 0x09.
```
