gatt-client: CCCD synthesized without discovery when a notify characteristic has exactly one descriptor; write to the non-CCCD attribute causes ATT timeout and connection teardown

## Summary

`discover_descs()` in `src/shared/gatt-client.c` contains an optimization that
skips descriptor discovery when a notify/indicate characteristic has exactly one
descriptor handle, inserting a synthesized `0x2902` (CCCD) into the GATT db
without ever querying the device:

```c
if (desc_start == chrc_data->end_handle &&
        (chrc_data->properties & BT_GATT_CHRC_PROP_NOTIFY ||
         chrc_data->properties & BT_GATT_CHRC_PROP_INDICATE)) {
        bt_uuid_t ccc_uuid;

        /* If there is only one descriptor that must be the CCC
         * in case either notify or indicate are supported.
         */
        bt_uuid16_create(&ccc_uuid, GATT_CLIENT_CHARAC_CFG_UUID);
        attr = gatt_db_insert_descriptor(client->db, desc_start,
                                        &ccc_uuid, 0, NULL,
                                        NULL, NULL);
        if (attr) {
                free(chrc_data);
                continue;
        }
}
```

(`src/shared/gatt-client.c`, `discover_descs()`, ~lines 775-795 on current
master.)

The assumption is justified by Core Spec Vol 3, Part G, §3.3.1.1 (notify/indicate
property ⇒ CCCD shall exist). A widely shipped device family violates that
clause: Telink TLSR825x-based GE Cync bulbs and switches, whose notify
characteristic's only descriptor is a `0x2901` Characteristic User Description
containing the string `"Status"`. No CCCD exists anywhere on the service.

The consequences compound:

1. BlueZ's db now contains a `0x2902` at a handle the device declares as
   `0x2901`. The device was never asked - no `FIND_INFORMATION_REQ` covering
   this handle appears on the wire.
2. `StartNotify` writes `01 00` into that attribute. The device either returns
   `WRITE_NOT_PERMITTED` (arguably correct for a read-only user description) or,
   on other attempts, answers nothing at all - a device-side ATT violation.
3. On the silent case, the unanswered Write Request runs into the 30-second ATT
   transaction timeout (Vol 3, Part F, §3.3.3) and the connection is torn down -
   a connection that was fully functional, with notifications streaming on the
   characteristic value handle throughout the wait.
4. The client sees `UNLIKELY_ERROR`, which does not correspond to anything sent
   on the wire. It is the local representation of the timeout, and is routinely
   misattributed to the device.
5. The synthesized descriptor is persisted to the GATT cache. Deleting the cache
   or `bluetoothctl remove` does not help: rediscovery skips the handle again and
   re-synthesizes the same `0x2902`.

## Environment

* **BlueZ:** 5.86 (the code path is present unchanged on current master,
  verified 2026-08)
* **Kernel:** 6.18.34-haos-raspi (aarch64), Home Assistant OS
* **Adapter:** Raspberry Pi 5 built-in - Broadcom **BCM4345C0**, firmware
  `003.001.025`, patch `brcm/BCM4345C0.raspberrypi,5-model-b.hcd`, HCI
  manufacturer `0x0131`, HCI version `0x09`
* **Device:** GE Cync, Telink TLSR825x SDK firmware. **Three units reproduce**,
  across **two OUI families** (`F4:BC:DA` and `78:6D:EB`). One of the three had
  never been connected before - a 28-byte cache file containing only `Name=` -
  and reproduced identically.

## The device's actual attribute table

Identical on both OUI families:

| handle | what the device declares | value returned by `READ_REQ` |
| :--- | :--- | :--- |
| `0x0011` | characteristic `…1911`, props `0x1a` (read/write/**notify**), value `0x0012` | |
| `0x0013` | `0x2901` | `53 74 61 74 75 73` = `"Status"` |
| `0x0014` | characteristic `…1912`, value `0x0015` | |
| `0x0016` | `0x2901` | `"Command"` |
| `0x0017` | characteristic `…1913`, value `0x0018` | |
| `0x0019` | `0x2901` | `"OTA"` |
| `0x001a` | characteristic `…1914`, value `0x001b` | |
| `0x001c` | `0x2901` | `"Pair"` |

One `0x2901` per characteristic, each named after its function. No `0x2902`
anywhere. Independently corroborated by CoreBluetooth on the same hardware,
whose `discoverDescriptors` returns exactly one descriptor - `0x2901` - and whose
`setNotifyValue` therefore fails with "the attribute could not be found" while
**leaving the connection up**. (macOS exposes peripherals as opaque per-host
UUIDs rather than BD_ADDRs, so those units are from the same account and product
line but their OUI family cannot be identified from that host. The Linux-side
results above cover both families explicitly.)

## Reproduction

1. Connect and let `gatt-client` run discovery (observed through
   bleak/`StartNotify`, but the behavior is in `shared/gatt-client`, not the
   binding).

2. Observe in btmon: descriptor discovery issues `FIND_INFORMATION_REQ` for
   `0x0004`, `0x0016`, `0x0019`, `0x001c` - the descriptor slot following every
   *other* characteristic - and **never for `0x0013`**, the sole descriptor of
   the notify characteristic. The discriminator is exact: the only slot skipped
   is the one following the only characteristic declaring `notify`.

3. `ATT READ_REQ handle=0x0013` returns `53 74 61 74 75 73` (`"Status"`) - an
   ordinary, successful exchange.

4. Call `StartNotify`. btmon shows `WRITE_REQ handle=0x0013 value=0100`.
   **Outcome varies by attempt on the same device and the same firmware:**
   either `WRITE_NOT_PERMITTED`, or no response of any kind followed ~30s later
   by teardown. Across 14 measured attempts in two runs, every one ended in
   `UNLIKELY_ERROR` after a consistent ~30 second delay, and the link was held
   for the full window in **zero** of them - while 17-19 notifications arrived
   per attempt, decoding to status for 34-38 distinct mesh device ids.

5. Inspect `/var/lib/bluetooth/<adapter>/cache/<MAC>`:

   ```
   0011=2803:0012:1a:…1911
   0013=00002902-…            ← synthesized; device declares 2901 here
   0016=00002901-…
   0019=00002901-…
   001c=00002901-…
   ```

   Every descriptor BlueZ actually discovered is `2901`. The one it never asked
   about is `2902`.

6. `bluetoothctl remove <MAC>`, confirm the cache file is gone, reconnect:
   identical result, and BlueZ writes `0013=00002902-…` back into a freshly
   created cache having still issued no `FIND_INFORMATION` for it. The entry is
   regenerated by the optimization on every discovery, not restored from stale
   state.

## Control: the same session with the CCCD write removed

Driven over `HCI_CHANNEL_USER` so the application is its own ATT client and
BlueZ is not in the path. The notification subscriber is registered locally;
`subscribe()` is never called, because it performs the same CCCD write:

```
notifications received : 21
distinct devices seen  : 42
CCCD writes performed  : 0
connection still up    : True
```

Same device, same handles, same pairing handshake. The only two writes on the
wire are the vendor's pairing exchange (`0x001b`) and its enable write
(`0x0012`), both acknowledged normally. **Notifications arrive without any CCCD
write**, which is also why they keep arriving during the 30-second timeout on
the BlueZ path - the pending write is not what enables them.

## Expected vs. actual

**Expected:** either the descriptor's actual type is established before a value
implying CCCD semantics is written into it, or a failed/unanswered write to an
assumed descriptor degrades the `StartNotify` operation rather than costing the
connection.

**Actual:** an undiscovered attribute is recorded and cached as `0x2902` on
inference alone; the resulting write can trip the ATT transaction timeout; a
connection carrying live notification traffic is torn down; and the surfaced
error implicates the device for a sequence it never participated in as assumed.

## Notes on fault attribution

The device is non-compliant twice over: `notify` without any CCCD (Vol 3, Part G
§3.3.1.1), and a Write Request left unanswered (Vol 3, Part F). The ATT-timeout
teardown itself is spec-mandated and not at issue.

This report is about the step before that - BlueZ manufacturing the descriptor
without verification, with no way for a client to detect that the `0x2902` it is
shown was inferred rather than discovered, and no API-level way to avoid the
write (`StartNotify`/`AcquireNotify` perform it unconditionally). Stacks that
discover rather than infer fail this device gracefully: the subscribe errors and
the link survives.

## Possible directions

* **Verify the assumption.** A single `FIND_INFORMATION_REQ` on the lone handle
  costs exactly the round trip the optimization saves, once per discovery.
* **Or mark synthesized descriptors as unverified** in the db, and on a
  failed/timed-out CCCD write to an unverified descriptor, fail the notify
  registration without escalating to the connection.
* **Or lazily verify** on first `StartNotify` for characteristics whose CCCD was
  assumed.

## Attachments

* `capture-A-startnotify-failing.txt` - the failing path. Two sessions: one where
  the vendor enable write is acknowledged and 20 notifications stream while the
  `0x0013` write goes unanswered; one where pairing completes without an enable
  write and no notifications arrive. Together they isolate what actually starts
  the reporting.
* `capture-B-handle-0x0013-read.txt` - `READ_REQ 1300` → `"Status"`.
* `capture-C-after-cache-removal.txt` - discovery after `bluetoothctl remove`,
  still skipping `0x0013`.
* `capture-D-raw-hci-no-cccd-works.txt` - the control above: 0 writes to
  `0x0013`, 21 notifications.
* `cache-gatt-db.txt` - the regenerated cache entry with the synthesized line in
  context.

* `btmon-startnotify.btsnoop` - binary trace of the reproduction, attached in
  the trace field. Captured **without performing the vendor pairing at all**,
  since the defect needs no authenticated session: discovery skipping `0x0013`,
  the read returning `"Status"`, and the `0100` write, in one connection.

Decoded captures come from the HCI monitor socket (`HCI_CHANNEL_MONITOR`, the
same feed btmon consumes). Where a pairing exchange appears in them it is
redacted - it is a crypto input to the device's session key and irrelevant to
the defect; handles, opcodes and the CCCD write are untouched. Device MACs
available on request.

Happy to test patches against the physical devices.
