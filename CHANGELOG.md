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
