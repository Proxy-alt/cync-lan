"""Compose DRAFT-ISSUE.md from ISSUE.md, so the draft cannot drift from the source.

bluez/bluez uses an issue *form* (`.github/ISSUE_TEMPLATE/issue.yml`), seven
fields. The description alone is ~8 kB, ~12 kB once URL-encoded, so a prefilled
`?description=...` link is not viable - and the trace has to be dragged into the
browser regardless. So the deliverable is a draft to paste from, one clearly
delimited block per field.

Run:  python3 make-draft.py
"""

from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).parent


def drop_section(text: str, header: str) -> str:
    """Remove a '## Header' section, up to the next '## '."""
    out, skipping = [], False
    for line in text.split("\n"):
        if line.startswith("## "):
            skipping = line.strip() == header
        if not skipping:
            out.append(line)
    return "\n".join(out)


source = (HERE / "ISSUE.md").read_text()
lines = source.split("\n")
title = lines[0].strip()

# Environment goes in the Versions field, Attachments into the upload widgets.
description = drop_section(drop_section("\n".join(lines[1:]), "## Environment"),
                           "## Attachments")
description = re.sub(r"\n{3,}", "\n\n", description).strip()

REPRODUCE = """1. Connect to the device and let gatt-client run discovery (observed via
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
   created cache having still issued no FIND_INFORMATION for it."""

OTHER_LOGS = """Decoded ATT exchanges, captured from the HCI monitor socket
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

  0001-gatt-client-discover-the-CCC-descriptor.patch
      The proposed fix - removes the synthesis in discover_descs() so the
      descriptor is discovered. Applies cleanly to master; not built or run.

A btsnoop of the same reproduction is attached in the trace field above. It
was captured without performing the vendor pairing at all, so it carries no
key material - the defect does not require an authenticated session."""

VERSIONS = """- BlueZ version: 5.86 (the discover_descs() single-descriptor optimization is
  present unchanged on current master, verified 2026-08)
- Kernel version: 6.18.34-haos-raspi (aarch64), Home Assistant OS
- Problematic device: GE Cync bulb/switch, Telink TLSR825x SDK firmware.
  Three units reproduce across two OUI families (F4:BC:DA and 78:6D:EB), which
  carry an identical attribute table. One of the three had never been connected
  before - a 28-byte cache file containing only Name= - and reproduced
  identically, which rules out stale cached state.
  Adapter: Raspberry Pi 5 built-in, Broadcom BCM4345C0, firmware 003.001.025,
  patch brcm/BCM4345C0.raspberrypi,5-model-b.hcd, HCI manufacturer 0x0131,
  HCI version 0x09."""

BAR = "=" * 78

draft = f"""# Draft: the issue as it will be filed

Generated by `make-draft.py` from `ISSUE.md` - edit that, not this.

Open https://github.com/bluez/bluez/issues/new?template=issue.yml and fill the
seven fields below. Everything between a `{BAR[:20]}` pair is one field's exact
contents. The trace and the other files must be dragged into the browser, so
this cannot be filed from the CLI.

## Title

{title}

## Field 1 - Description *(required)*

{BAR}
{description}
{BAR}

## Field 2 - To reproduce

{BAR}
{REPRODUCE}
{BAR}

## Field 3 - btmon trace

Attach **btmon-startnotify.log** (568,965 bytes).

Safe to publish, and safe by construction rather than by redaction: the defect
needs no authenticated session, so the capture was taken with the vendor
pairing never performed and with `cync_ble` disabled so nothing else on the
host could pair during the window. Verified - zero writes to `0x001b`, the
pairing handle.

The `.log` extension matters: GitHub's uploader rejects `.btsnoop`, and the
field's own instructions say `btmon -w btmon.log`. The contents are an ordinary
btsnoop binary either way.

## Field 4 - Analysis focus

{BAR}
GATT discovery
{BAR}

Not `Disconnection analysis`. The teardown is downstream of the discovery gap,
and the auto-analyser will otherwise spend itself on the consequence.

## Field 5 - Privacy

    [ ] Skip anonymization          <- leave UNCHECKED
    [x] I understand this trace ... <- tick, since a trace is attached

Leave anonymization on regardless. The trace carries no pairing material, but
the mesh name travels as the BLE device *name* in advertisements, and it is a
crypto input to the session key - name scrubbing is exactly what the default
covers.

## Field 6 - Other logs

Attach the five `.txt` files and the `.patch`, then paste this into the box
(the field renders as plain text):

{BAR}
{OTHER_LOGS}
{BAR}

## Field 7 - Versions

{BAR}
{VERSIONS}
{BAR}
"""

(HERE / "DRAFT-ISSUE.md").write_text(draft)
print(f"wrote DRAFT-ISSUE.md  ({len(draft)} bytes, description {len(description)} bytes)")
