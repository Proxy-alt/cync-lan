# Changelog

Version history for the `cync-lan` core protocol library
(this package's `pyproject.toml` `version` field). Independent of the
`cync-lan-mqtt` Docker/MQTT add-on's own version scheme and the Home
Assistant `cync_lan` custom_component's own version scheme - all three are
versioned and released separately. See the root `README.md`/`RELEASING.md`
on `feature/ha-custom-component` for how the three artifacts relate.

### 0.1.2

- Fix `protocols.py`'s `MqttSink.pub_online` being declared as a plain
  `def` instead of `async def` - every real implementation (the MQTT
  add-on's `MQTTClient.pub_online`, the HA integration's
  `CyncLanBridge.pub_online`) is async, since `devices.py` wraps this call
  in `asyncio.create_task()`, which requires a coroutine. Caught by running
  the HA integration's own strict-mypy pass against this package - the
  mismatch was invisible at runtime (Python doesn't enforce `Protocol`
  conformance dynamically) but broke static type-checking for anything
  assigning a real implementation to `GlobalObject.mqtt_client`.

### 0.1.1

- No functional change - verifies the CI publish workflow's PyPI Trusted
  Publishing step end-to-end now that the `cync-lan` project exists on
  PyPI (0.1.0 was published manually after the pending publisher wasn't
  yet recognized on the first automated attempt).

### 0.3.0

**New: five more hub commands**, all with op_codes and request payloads read
from the decompiled app rather than guessed:

| Function | op_code | What it does |
|---|---|---|
| `query_hub_info()` | `0x4B` | Firmware version, MAC and setup code |
| `query_device_time()` | `0x46` | The clock the hub believes it is running on |
| `query_sol_config()` | `0xAD` | A Sol lamp's clock/timer/mic-light display flags |
| `delete_automation()` | `0x97` | Removes a Schedule's trigger binding |
| `delete_group()` | `0x32` | Deletes a device group from the mesh |

`delete_automation` closes a real gap: `create_schedule`, `toggle_automation`
and `delete_schedule` all existed, but nothing removed the binding
`add_automation` creates.

`query_device_time` is worth calling out - native Cync Schedules fire off the
hub's own clock, not Home Assistant's, so a hub whose time has drifted runs
its automations at the wrong moment and nothing else exposed that.

As with the rest of this family, each `cmd_code` is PREDICTED from the length
formula and the reply channel is unconfirmed, so a query returning `None` on
timeout is an expected outcome rather than an error.

**Deliberately not implemented**, and documented in `docs/mesh_opcodes.md`
instead:

- `0x49` `QueryHubFirmwareUpdates` - its reply is a variable-length list of
  per-device records rather than a fixed layout, and a wrong record stride
  produces plausible-looking garbage rather than an obvious failure. There is
  no capture to validate a decoder against.
- `0x4F` `StartHubFirmwareUpdates`, `StartWifiOtaUpdate` and
  `SetWifiOtaUpdateMode` - these flash firmware. Everywhere else in this
  family a wrong predicted `cmd_code` means the device ignores the packet;
  here the same mistake has a far worse floor, so no code path capable of
  sending them exists.

### 0.2.1

**Fixed: this package could not be installed alongside Home Assistant.**
`pyyaml` was pinned to exactly `==6.0.2`, while Home Assistant requires
`PyYAML==6.0.3`. The two are mutually exclusive, so `pip install cync-lan
homeassistant` failed outright with a resolution error - which also meant the
Home Assistant integration that depends on this package could not have its
requirements installed. Relaxed to `>=6.0.2`.

Nothing in this package needs an exact PyYAML version; it only calls
`safe_load`/`dump`.

### 0.2.0

**Minimum Python is now 3.12.** The package previously declared `>=3.9`, but
that was never true: `structs.py` imports `enum.StrEnum` (3.11+) and
`devices.py` uses a PEP 701 nested-quote f-string (3.12+). Installing on
3.9-3.11 resolved and then failed on the first import. The declared floor now
matches what the code actually needs, and CI runs against it.

**Fixed: six hub commands sent a malformed length field.** `cmd_code` is the
byte length of everything after the packet header, and `create_scene`,
`create_schedule`, `delete_scene`, `delete_schedule`, `toggle_automation` and
`add_automation` all computed it one byte short. A short length field makes
device firmware read a truncated body, which presents as the command silently
doing nothing. If you tried these and nothing happened, this is why. They are
still EXPERIMENTAL - the fix makes the framing correct, it does not confirm
the commands work on real hardware.

`scripts/cmd_code.py` computes the field for a new command and audits every
existing one against it; the audit runs in CI. It is what found this.

**Fixed: three crashes on error and shutdown paths.**

- A network failure during token refresh or OTP submission raised
  `NameError: name 'lp' is not defined` instead of reporting a clean auth
  failure. `aiohttp`'s connection and timeout errors are not
  `ClientResponseError`, so they hit the generic handler, which referenced a
  variable that was never defined there.
- Stopping MITM mode raised `NameError: name 'name' is not defined` from the
  cancellation handler, so the proxy task never shut down cleanly.
- Closing a connection with mismatched state raised `NameError` from a
  malformed f-string.

**Fixed: MITM mode spun a CPU core after the cloud disconnected.** The proxy
loop treated an empty read as "nothing to do" and looped. A stream returns
empty forever once the peer closes, so this ran flat out for as long as MITM
stayed enabled. It now stops on EOF.

**Fixed: the packet parser could freeze for 3 seconds per device.** Checking a
device's retained MITM state opened a blocking broker connection from inside
the inbound packet parser, stalling all other devices' traffic for the
duration. Moved off the event loop.

**Added: `query_hub_mesh_credentials()`** - reads the BTLE mesh name and
password from a connected hub (op_code `0x8A`). These are the two values
`ble_provision`'s key derivation needs, so this is what allows provisioning a
new device onto an *existing* mesh rather than only a factory-default one.
EXPERIMENTAL: the response channel is unconfirmed and may time out.

**Removed** `parse_packet_OLD`, 769 lines of superseded dead code.

**Removed** `nCyncServer.loop`. It was assigned in `__init__` and never read
anywhere, and `asyncio.get_event_loop()` raises when no loop is current - so
constructing the server outside a running loop crashed. Anything needing the
loop should call `get_running_loop()` at the point of use.

Housekeeping: ruff now runs in CI (it was configured but had never been run -
444 violations, including the three undefined names above). Tests run on every
push and pull request, not only on a version bump. `server.py` went from no
test coverage to 80%; the suite is 123 -> 157 tests.

### 0.1.0

- First published release. Extracted from what was previously vendored
  directly into the Home Assistant custom_component
  (`custom_components/cync_lan/vendor/cync_lan/`) and duplicated in the
  `cync-lan-mqtt` add-on's own source tree - both now depend on this
  package from PyPI instead. Contains the device/session TCP state machine
  (`devices.py`, `server.py`), the packet codec (`packet/`), Cync cloud
  auth (`cloud_api.py`), BLE GATT provisioning (`ble_provision.py`), and
  shared config/constants (`const.py`, `structs.py`, `utils.py`,
  `metadata/`). Ships a `py.typed` marker. `GlobalObject.mqtt_client`/
  `.export_server` are now typed against `Protocol` classes in the new
  `protocols.py` instead of importing the add-on's concrete types directly
  (this package doesn't depend on either consumer package).
