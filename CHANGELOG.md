# Changelog

Version history for the `cync-lan` core protocol library
(this package's `pyproject.toml` `version` field). Independent of the
`cync-lan-mqtt` Docker/MQTT add-on's own version scheme and the Home
Assistant `cync_lan` custom_component's own version scheme - all three are
versioned and released separately. See the root `README.md`/`RELEASING.md`
on `feature/ha-custom-component` for how the three artifacts relate.

### 0.6.0

**New: a BLE mesh transport, confirmed on real hardware.** `cync_lan.ble_mesh`
controls already-provisioned devices over Bluetooth instead of over the TCP
relay. It is the sibling of `ble_provision`, which gets a factory-default device
*onto* a mesh — same Telink protocol, same opcode table, different framing.

This matters beyond adding a second way to send a command. **The BLE path needs
neither DNS redirection nor the hub command family.** Those are the two things
that constrain the TCP path: DNS redirection is the setup requirement behind most
support traffic, and hub commands currently get no reply at all (see
`docs/hub_envelope_ab_test.md`). Neither is on the critical path here.

What was verified against a wired Cync switch, not reasoned about:

- the session handshake, with `verify_pairing_response` reporting mutual auth
  **verified** — the device proved it derived the same key material;
- `set_power` (`0xD0`) **changed the switch's state**, with cync-lan reporting
  that change over its own TCP connection. Command out over one transport,
  confirmation back over an independent one, so the result cannot be a false
  positive;
- brightness, in both the `0xF0` and `0xD2` forms;
- **mesh relay** — a command addressed to one device and sent over a connection
  to a *different* device is relayed and acted on. One session therefore reaches
  the whole mesh, which is what makes this usable at forty-odd nodes rather than
  needing a link per device;
- the mesh credentials come from the cloud export this library already writes:
  the home's `mac` is the Telink mesh name and its `access_key` is the mesh
  password. `mesh_credentials_from_home()` pulls both out. Nothing about BLE
  needs the hub, despite what the `query_mesh_credentials` button implies.

Not confirmed, and marked as such in the code: colour temperature and RGB. They
ride the same `0xF0` family whose brightness member works, so they are better
founded than a guess, but nobody has moved either over this transport. Status
notifications are worse than unconfirmed — at least one firmware declares
`notify` on its characteristic, rejects the subscription with GATT `Unlikely
Error`, and drops the connection. `subscribe()` reports that rather than raising,
and sending never depends on it.

`ble_mesh` deliberately never imports `bleak`. It takes a `GattClient`
(a `typing.Protocol`) rather than constructing one, so a Home Assistant
integration can hand in a connection from HA's own Bluetooth stack — which is
what makes ESPHome Bluetooth proxies work. Building a client internally would
have limited the transport to devices in radio range of the host and quietly
ruled proxies out.

Two smaller things:

- **The `0x11, 0x02` prefix on every documented payload is identified**: it is
  the Telink vendor ID `0x0211`, little-endian. The TCP transport embeds it at
  the head of the payload; BLE gives it a field of its own. That is why the
  opcode table is shared between the two rather than duplicated, and it is now
  confirmed on hardware for two separate opcode families.
- **Device type 54 added**, as `supported=False` and marked plausible rather
  than confirmed. It was a gap in an otherwise dense run, and
  juanboro/cync2mqtt lists it beside 37 and 49 in the built-in-occupancy switch
  family. That is agreement between two reverse-engineering efforts, not
  hardware truth — and this project's own capture-confirmed third member of that
  family is type 56, which keeps its supported status.

### 0.5.3

**Fixed: the version this library reports was wrong, and had been for a
while.** `__version__` was a hand-maintained string that nothing in the release
process touched, so it still read `0.1.2` at release 0.5.2 - a version that was
never published at all.

That number is not decorative. `CYNC_VERSION` is built from it and reaches
users in three places:

- the startup line (`CyncLAN (version: ...) stack initializing`), which is
  exactly what the bug report template asks people to paste;
- the output of `cync-lan -V`;
- the `sw_version` shown on every device page in Home Assistant.

All three have been reporting a fiction, which quietly undermines any bug
report that depends on knowing what someone is actually running. **If you have
reported an issue before, the version you gave was almost certainly wrong** -
not your fault.

`__version__` now comes from the installed package metadata, so
`pyproject.toml` is the only place a version is declared and this cannot drift
again. A source tree that was never installed reports `0.0.0+unknown` rather
than inventing a plausible-looking number.

Also stops tracking `build/lib/`, fifteen files of stale setuptools output that
had diverged from `src/` - it still carried the old hand-written version. No
effect on the published package.

### 0.5.2

Docstring only - no behaviour change.

`set_indicator_led`'s docstring opened with "EXPERIMENTAL:" while its own body
said "CONFIRMED WORKING on real hardware with the corrected op/payload" and
explained that it is deliberately not flagged by `_warn_experimental_cmd_code`,
because both its op_code and cmd_code are confirmed. The first word contradicted
the rest and was the one people read.

### 0.5.1

Packaging metadata only - no code change.

Adds the `keywords` and the `Topic ::` / `Framework ::` / `Development Status`
classifiers that PyPI weights in its own search ranking. `cync-lan` previously
declared none of them, so it was effectively unfindable on PyPI except by
exact name.

### 0.5.0

**New: an opt-in alternate envelope for the hub command family**, selected with
the `CYNC_HUB_ENVELOPE` environment variable. Default is `routed`, which is
byte-for-byte what has shipped since 0.3.0; `bare` is the alternative.

This exists to settle a question, not because anything is known to be broken.
Hub commands - scenes, schedules, automations, groups, and the hub queries -
currently go out as `header(8) + routing(7) + op(1) + payload`, with the
7-byte block that addresses a mesh device. A pass over the decompiled phone
app found that all fifteen of its hub command classes bypass the method that
prepends that block entirely, which makes structural sense: a hub command is
not addressed to a mesh device, so there is nothing to route to.

That is suggestive, not decisive. The app's path is phone-to-device, while
this library sits on the device-to-cloud side, so the evidence does not
transfer automatically. Setting `CYNC_HUB_ENVELOPE=bare` sends
`header(8) + op(1) + payload` with the length field 7 shorter, so the two
shapes can be compared against real hardware instead of argued about. The
exact bytes each produces, for all six shipped hub commands, are tabulated in
`docs/hub_envelope_ab_test.md`.

Unlike every other `CYNC_*` setting, this one is re-read on each command
rather than frozen when `cync_lan.const` is imported. Flipping between the two
arms has to be cheap, or the second arm never gets run.

Internally, the length field and the routing flag now come from a single
helper instead of eleven hardcoded `8 + len(payload)` expressions. They have
to move together - the length counts the bytes after the header, so dropping
routing drops exactly 7 from it - and a packet whose declared length disagrees
with its body is considerably harder to diagnose than either envelope simply
being the wrong choice.

No behaviour changes unless you set the variable.

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

### 0.4.0

**New: `CyncDevice.identify()`** - makes a device announce itself physically,
so you can tell which bulb or switch an entity actually is. Payload is
`{0xF7,0x11,0x02,0x03}` plus 1 to start or 2 to stop, on the same dispatch
path as `set_indicator_led` - the one command in this family confirmed
working on real hardware, which makes this the most likely of the new
commands to work.

**New: dimmer level-bar LEDs.** `set_dimmer_led_mode()` (0xF7/0x62) and
`set_dimmer_led_brightness()` (0xF7/0x63). These are the row of level LEDs on
a dimmer switch, not the small status LED `set_indicator_led` controls.

`DimmingLedsIndicatorMode` has exactly two values, `BRIEFLY_DISPLAY` and
`ALWAYS_ON` - there is no "off", so the bar cannot be disabled, only made
momentary. Brightness deliberately sends **two** packets, Preview then Save,
because only Preview carries a level; a lone Save would commit whatever the
device happened to be previewing.

**New: `set_time()`** - sets the clock a hub runs its native Schedules from,
the counterpart to 0.3.0's `query_device_time()`. A drifted hub runs its
Schedules at the wrong moment and nothing on the Home Assistant side can
compensate.

Its DST byte reproduces a quirk rather than improving on it: the app decides
by testing whether the timezone id starts with `"America"`, and writes
minutes=0/flag=1 in that case. That is a string prefix test, not a DST
calculation, so `us_style_dst` is exposed to override it.

All three carry the usual caveat for this family: the `cmd_code` is predicted
from the length formula rather than confirmed against a capture.

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
