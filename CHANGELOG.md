### 0.1.1

- No functional change - verifies the CI publish workflow's PyPI Trusted
  Publishing step end-to-end now that the `cync-lan-mqtt` project exists
  on PyPI (0.1.0 was published manually after the pending publisher wasn't
  yet recognized on the first automated attempt).

### 0.1.0

- Renamed from `cync-lan` (version scheme `0.0.6bNN`) to `cync-lan-mqtt`,
  and reset to a clean `0.1.0` - the protocol/device layer (`devices.py`,
  `server.py`, `cloud_api.py`, `packet/`, `metadata/`, `ble_provision.py`,
  most of `const.py`/`structs.py`/`utils.py`) moved out to a new, separately
  published `cync-lan` core package (see its own `CHANGELOG.md` on the
  `core` branch) instead of living in this same package. This package now
  contains only the standalone daemon (`main.py`), MQTT/HASS-discovery
  bridge (`mqtt_client.py`), and HTTP device-list exporter (`exporter.py`),
  and depends on `cync-lan` from PyPI. The `cync-lan` console script name,
  every environment variable, and all runtime behavior are unchanged - this
  is a packaging change, not a functional one. Earlier history below this
  entry describes the same codebase before the split.

### 0.0.6b48
- Add `CyncDevice.relay_source` tracking: whichever TCP-connected device most recently relayed a
  status update for a given device, set at every mesh status/MeshInfo parse site. The only presence
  signal available for a BTLE-mesh-only device, which never owns a direct TCP connection of its
  own - not yet exposed as an MQTT topic here, but backs the HA custom_component's new "Connected
  via" diagnostic sensor (see its own `CHANGELOG.md`).

### 0.0.6b47
- Fix devices going unavailable when they lose power or network not being detected - `close()`
  tore down a TCP session's socket resources but never marked the device that owned it offline,
  even in the exact case the code already detected and logged ("device probably dropped the
  connection (lost power)"). A device that simply stopped appearing in any mesh status broadcast
  (rather than appearing WITH a "not recently seen" flag - the only case the existing offline
  detection covered) stayed marked online/available indefinitely, showing stale last-known state.
  Now `close()` marks that session's own device offline immediately whenever it ends, covering
  lost power, network drops, and deliberate reconnect cycles (MITM mode toggling briefly flips
  availability too - correct, not a regression, since that's a real disconnect/reconnect).

### 0.0.6b46
- Add `cync-lan-ble-provision`, an EXPERIMENTAL, untested-against-real-hardware CLI for pairing a
  brand-new/factory-reset device onto a mesh directly over BLE (a separate transport entirely from
  this project's usual TCP relay). Implements the full confirmed pairing/session-key/mesh-credential
  handoff flow, including the exact fixed bootstrap bytes the real Cync app uses for a never-
  provisioned device - independently reproduced from the documented formula, matching the
  decompiled app's own hardcoded constant exactly. See `docs/ble_provisioning_protocol.md`. Install
  with `pip install cync_lan[ble]`; does not touch the main server or require `bleak` otherwise.
- Fixed, before this saw real use: the pairing-confirmation check accepted any nonzero response
  byte as success; the real app's own callback (confirmed via direct source read) only treats the
  literal byte value `7` as confirmed - anything else, including plausible-looking nonzero values,
  means the device rejected the new mesh credentials. Also added `verify_pairing_response()`, a
  non-fatal diagnostic replicating a real mutual-auth check the app performs that this module had
  incorrectly assumed (based only on an unrelated open-source client) wasn't done at all.

### 0.0.6b45
- Fix sol-lamp brightness changes not updating in HA immediately: the ack-matching allow-list was missing the `0xD2` op sol-lamp devices use for brightness, so their acks went unrecognized and HA's brightness slider stayed stale until an unrelated status update happened to correct it. Confirmed against the real Cync Android app's decompiled command encoding
- Fix the "fireworks" light-show effect sending the wrong effect ID (`0x3A`/58, not valid anywhere in the real app's effect scheme) instead of the correct ID (`3`) - likely silently rejected by real hardware before this fix
- Reclassify every 4-wire wired switch type (dimmer, toggle, circle, paddle, motion-sensing, keypad, no-neutral and TCO variants) from `light` to `switch`, and remove incorrectly-claimed color/tunable-white (and in some cases dimmable) capabilities - cross-referenced against the real Cync Android app's device-type data, which confirmed none of these switch types actually support color, and several don't dim at all. Also corrected two swapped model names (types 52/53 Toggle vs Circle, and the "Paddle" label between types 48/125)
- Recognize 54 additional Cync device types that were completely missing from the device-type table (newer-generation bulbs, switch Gen2/Gen3/TCO variants, outdoor/TCO plugs, a second fan controller and thermostat variant, dynamic-effects fixtures, wafer downlights, wire-free remotes, and cameras) - sourced from the full real Cync Android app device-type catalog

### 0.0.6b43
- Support deviceType 112 "Wireless Switch" for real. Previously marked unsupported after a toggle test showed nothing in the debug log, but that was a false negative - a real capture confirms it sends a normal status packet when pressed (recently_seen goes 1->0 ~19s later, same shape as the type-96 motion sensor's trigger flag). Now exposed as an `occupancy` binary_sensor via the existing motion-sensor pipeline

### 0.0.6b42
- Fix every entity showing a blank Entity ID in HASS. HASS deprecated using `object_id` to set an entity's ID and now requires `default_entity_id` to be the *full* domain-prefixed entity_id (e.g. `light.cync_lan_...`), not a bare slug - this project was sending the bare slug for every entity, which HASS silently rejected. **Existing entities won't rename themselves** - delete them from the HA entity registry (or delete the Cync-LAN Bridge device and your Cync devices, then let discovery republish) to pick up a valid entity_id

### 0.0.6b41
- Fix the raw-debug broadcast status decoder silently dropping a device from its output whenever a `0x2e` byte happened to appear anywhere in the packet (an incorrect heuristic bumped the per-device chunk size from 19 to 20 bytes). Confirmed via a real 2-device broadcast capture where only the first device decoded; the fix always uses the correct 19-byte size. Debug-output only - doesn't affect live device state or MQTT, but it's the exact tool used to read raw captures for bug reports

### 0.0.6b40
- Recognize deviceType 112 "Wireless Switch" (battery-powered BTLE scene remote with a status LED ring) instead of reporting it as never-seen-before. It's now known but marked unsupported - a live capture test showed pressing it produces no packet visible to the bridge, so it likely drives its paired light directly over the BTLE mesh; nothing to implement yet without real packet data

### 0.0.6b39
- Fix a silent data-loss bug found via the unsupported-device capture tool: a stray/misaligned leading byte in a TCP read caused the entire rest of that read - which could be a large, fully valid burst of real device data - to be discarded instead of just the bad byte(s). Confirmed via a real capture where 4 junk bytes preceded a legitimate MeshInfo update covering ~40 devices; now resyncs to the next recognized packet instead of dropping everything after the bad byte

### 0.0.6b38
- Fix standalone BTLE-only accessories (e.g. motion sensors, type 96) being silently dropped from the exported config entirely. The cloud export required a `wifiMac` for every device, but these accessories have no WiFi radio and never have one - confirmed via a real export showing a motion sensor's raw entry with no `wifiMac` field at all. Now optional; devices that lack WiFi already route around it downstream
- Fix motion sensor `binary_sensor` entities showing "Unknown" in HASS until their first real detection ever fired. State publishes are now retained, and a retained OFF is seeded at discovery time if nothing has been published yet

### 0.0.6b37
- Real motion sensor support, from two independently-confirmed real-world captures: standalone Cync motion sensor accessories (type 96) now show up as a proper `binary_sensor` (occupancy), and light/switch models with a built-in occupancy sensor ("...with Motion and Ambient Light", types 37/49/56) get an extra motion `binary_sensor` alongside their existing light entity

### 0.0.6b36
- Handle `f9 af` mesh-status-ack confirmations: the device's response to the server's `f8 af` ack after each MeshInfo page was unrecognized, causing `capture_unknown_packet` entries in the unsupported-devices log. Now silently consumed (the packet is just an acknowledgment, nothing actionable)
- Fix `capture_unsupported_device` flooding the unsupported-devices log with hundreds of `dev_id=0` false-positives per minute. Root cause: the function had no concept of mesh broadcast pseudo-IDs. In the Cync BTLE mesh protocol, device IDs 1–255 are addressable nodes; ID 0 is the reserved broadcast/group address that every bridge re-broadcasts on every state-change cycle. The filter lives in the gatekeeper function itself so all callers benefit

### 0.0.6b35
- Fix another silently-dropped case found by the same capture tool: a full mesh-wide status dump (covering many devices at once) delivered via `0x83` instead of the usual `0x73` was never parsed either. Same inner format as the already-working `0x73` case, now recognized on both

### 0.0.6b34
- Fix a substantial source of silently-dropped device state updates: devices sometimes deliver their status wrapped in a `0x73` packet instead of the usual `0x83`, using the exact same inner format - nothing recognized this variant, so it was only ever acknowledged, never parsed. Found via the new debug capture tool (97% of one capture session was this single pattern); confirmed by hand-decoding a real sample back to a known device with sensible values

### 0.0.6b33
- New "Unsupported Device Debug Capture" option: logs raw packets from never-seen or unsupported device types to a dedicated file (`unsupported_devices.log`), independent of Raw Debug. Safe to leave on for an extended/overnight capture without the noise of full raw debugging - useful for gathering data to get a new device type properly supported
- The capture also now covers packets that don't resolve to a device ID at all (an unrecognized top-level packet header, or unrecognized control bytes on a `0x83`/`0x73` packet) - not something most people will ever need, but useful if you're gathering data to report a new/unimplemented device type to the maintainer

### 0.0.6b32
- Fix "Unknown packet header" warnings and the real device status updates they were silently discarding. Root-caused via a real capture: when a TCP read boundary split a packet's header across two reads, the short fragment got processed as "complete" instead of buffered, misaligning everything that followed in the next read

### 0.0.6b31
- Fix a crash that could permanently kill MQTT (state updates and commands both stop working until a manual restart) when a command was sent to a bridge device that hadn't finished identifying itself yet. Found via a real capture with raw_debug enabled; TCP device traffic kept working the whole time, only MQTT was affected

### 0.0.6b30
- Temporary diagnostic logging (raw_debug only) added to investigate a suspicious "unknown device ID: 0" status pattern with non-boolean field values (possibly a motion sensor or other unrecognized device type); no user-facing change

### 0.0.6b29
- The 0.0.6b28 "node_id MISMATCH" fix didn't hold up in a follow-up capture. Root-caused further: the underlying assumption (first MeshInfo entry = the requesting device) is simply false, not just mishandled pagination - confirmed by specific bridges consistently reporting the same "wrong" device across unrelated reconnects. Removed the check entirely rather than patch it again

### 0.0.6b28
- Fix spurious "node_id MISMATCH" warnings on paginated MeshInfo reconnects, confirmed via a fresh post-0.0.6b27 capture. Cosmetic only (no data was affected), but a real logic bug in how the parser detected "device announcing itself" across paginated dumps

### 0.0.6b27
- Fix spurious "unknown device ID: 0" warnings (and the state loss they implied) during MeshInfo parsing on reconnect. Root-caused to duplicate BTLE relay-path entries desyncing the parser's fixed-stride byte offsets; it now self-resyncs instead of reading garbage for the rest of that page

### 0.0.6b26
- Temporary diagnostic logging (raw_debug only) added to investigate spurious "unknown device ID: 0" warnings seen during a full mesh reconnect; no user-facing change

### 0.0.6b25
- New "Cync App Active" occupancy entity on the CyncLAN Bridge device — turns on when the Cync phone app connects to the BTLE mesh, auto-clears after 60s of inactivity

### 0.0.6b24
- One more benign broadcast pattern (`fa f0`) downgraded to debug alongside `fa af`, same app-BTLE-connect noise

### 0.0.6b23
- Two more benign `0x83` broadcast patterns (`fa af`, and `fa db` with a non-status sub-type) downgraded from WARNING spam to debug — both fire in bursts across many devices when the Cync phone app connects/disconnects from the BTLE mesh, not an actual problem

### 0.0.6b22
- Per-device "MITM Mode" switch entities no longer show up in HA by default (new `MITM Mode Entities` option, off by default). Existing installs will have any previously-created MITM switch entities automatically removed on next restart.

### 0.0.6b21
- Fix the bridge's "Should Restart" diagnostic entity carrying a leftover mismatched platform key from a copy-paste; harmless but incorrect

### 0.0.6b20
- Fix a few silent bugs found in a type/classification audit: 4 device types were missing their model number (typo in the source dropped it without error) and switches created without an explicit dimmable flag (plugs, fan controller, toggle switches) carried the wrong internal default. Neither was visible in HA, but both are now correct at the data level.

### 0.0.6b19
- Plugs/outlets now show up in HA with the outlet icon (`device_class: outlet`) instead of the generic switch toggle icon

### 0.0.6b18
- Fix duplicate entities in HA after a device's platform classification changes (e.g. the type 53 light->switch fix in 0.0.6b17 left the old `light` entity behind alongside the new `switch` one). Stale platform discovery configs now clear automatically on the next discovery announce, no manual entity removal needed in HA.

### 0.0.6b17
- Built from [Proxy-alt/cync-lan@python](https://github.com/Proxy-alt/cync-lan/tree/python) instead of upstream, pending PR back to baudneo/cync-lan
- Downgrade benign `0x83` broadcast warnings (unbound `fa 8e` control bytes, internal status for unmapped room/group IDs) to debug, they fired at WARNING for every device on every mesh broadcast despite being safe to ignore
- Add device type 36 (dimmable light switch) and 96 (standalone motion sensor, unsupported) so they show real metadata in HASS instead of "no metadata"
- Reclassify device type 53 "Toggle Switch" from a dimmable light to a binary switch, it's used interchangeably for fan- and light-wired switches with no dimming capability

### 0.0.6b16
- NOTE: always backup, I am a carpenter who does this in my spare time, not a software engineer
- Properly catch and parse fan controller state changes, was missing physical button presses. Thanks [@SamHartleyFixes](https://github.com/baudneo/cync-lan/commits?author=SamHartleyFixes)

### 0.0.6b15
- Cast int to str in order to encode UTF-8 in `set_fan_percentage`

### 0.0.6b14
- Rename dupe method: `set_fan_speed` -> `set_fan_percentage`

### 0.0.6b13
- Undocumented fan controller MQTT topic to test step size for speed; `set/raw_perc` topic accepts 0-100
  - Requires using MQTT publishing software (MQTT explorer, etc.) to send the payload, no entities in HASS UI 

### 0.0.6b12
- Add fan controller percentage slider state updates, will snap to quarter points
  - Percentage is converted to preset:
    - 0% = OFF
    - 1-25% = LOW
    - 26-50% = MEDIUM
    - 51-75% = HIGH
    - 76-100% = MAX
    - 2%, 13% or 23% will snap to 25% and be set to LOW

### 0.0.6b11
- Fixes for fan controller PRESET state updates; sub_id parameter missing

### 0.0.6b10
- Fix unbound `tgt_id`

### 0.0.6b9
- Add fan controller speed state syncing in HASS
  - Don't rely on HASS 'optimistic' state, use async callback pattern

### 0.0.6b8
- Add device/proxy connection watcher to close zombie TCP sessions gracefully
- Check for retained MITM button state messages on HASS discovery to persist state between reconnections
- Use a devices configured kelvin range for white temp conversions instead of hardcoded 2-7k range

### 0.0.6b7
- Fix command packet builder; restore changing brightness, white temp and RGB for non-plug devices

### 0.0.6b6
- Fix firmware parsing
- Fix non-awaited `remove_mitm_button()` method

### 0.0.6b5
- Fix dynamic firmware parsing and _update_app_stats
- Add verbose logging to track weird edge case

### 0.0.6b4
- Various CyncLAN lib fixes
- Add MQTT debug logging toggle in HASS app config
  - When disabled, it wont display debug level logs from the MQTT client WHEN the MAIN debug level is enabled, which can be very verbose when you have a lot of devices or have `raw_debug` enabled in the cync-lan config
  - When enabled, it will display debug level logs from the MQTT client, WHEN the MAIN debug level is enabled

### 0.0.6b3
- Fix stop_mitm() method, now reverts device back to normal operation on being turned off

### 0.0.6b2
- Fix unbound error on MITM activation

### 0.0.6b1
- Proxy / MITM mode: Devices connected to CyncLAN via TCP will have a 'MITM Mode' button exposed
  - Enabling will force the device to reconnect and all data will be proxied to the Cync cloud and logged to file and optionally to the log console
  - While MITM mode is enabled, the device will not be able to be used to send any commands to Cync devices, but it will still report device state changes (can read, no write)
  - It is recommended to have 1 TCP device connected that is not in MITM mode to be able to control the devices via HASS while you are gathering data on the MITM device
  - This is intended to be used to gather data on new devices and functionality that CyncLAN does not expose, so I can add support for it; dynamic / music / per segment lighting state/effects, thermostat, etc.
  - Currently only Cync devices support MITM mode, work is ongoing to allow Cync mobile apps and hopefully allow a way to add new devices to cloud accounts while network-wide DNS redirection is active.
  - See an example [work flow](https://github.com/baudneo/cync-lan/tree/python#workflow)
- Cloud API refresh token logic added
- Packet building logic refactoring
- Optimizations
- Add deviceType 151: Soft white decorative candle light (thanks [@tobyroworth](https://github.com/baudneo/cync-lan/commits?author=tobyroworth))

### 0.0.5b3
- add deviceTypes 53, 56, 155, 170

### 0.0.5b2
- BREAKING CHANGES:
  - Add encryption for the cloud token cache at rest; Fernet with static seeded PBKDF2HMAC key.
  - REQUIRES: Setting a random alphanumeric string for CYNC_SECRET_KEY in the App config

### 0.0.5b1
- BREAKING CHANGES:
  - Any automations or other logic relying on Cync switches being registered as `switch`es will need to be updated to use `light` instead.
- Cync switches are now exposed as `light` entities in Home Assistant.
  - This *should* allow for controlling Cync app rooms/groups; cync-lan will not be aware of what devices are part of what groups/rooms as the idea is to send the light switch commands (you will know what switch controls what devices).
  - You *should* be able to send the light switch RGB, brightness or white temp commands and the Cync group will change in unison, just like they do in the Cync app when you control a group/room, or a physical button press.
  - This assumes you have the switch setup to control the Cync devices logically instead of turning the mains power on/off to the circuit. It is untested with the latter.
  - Is there interest in instead of exposing switches as lights, expose the switches as switches and then also expose a light entity to control the group/room? Open an issue to discuss.
- Only the fan controller is still exposed as a switch, I dont have one to test if it can be grouped with other devices and targeted with RGB, etc. commands.
  - If you own a fan controller and can group it with other devices in the Cync app and then the fan controller can be used to control those Cync devices, please open an issue and we can try figuring something out. 

### 0.0.4b14
- DONT UPGRADE, this is a test for targeting cync groups/rooms
  - stephenwall-95 please test
  - typo: Light -> LIGHT

### 0.0.4b13
- DONT UPGRADE, this is a test for targeting cync groups/rooms
  - stephenwall-95 please test

### 0.0.4b12
- Add missing adjustable brightness to deviceType 125

### 0.0.4b11
- Add deviceType 128: 2700K A19 bulb

### 0.0.4b10
- Add deviceType 125: Paddle switch

### 0.0.4b9
- Add deviceType 40: Paddle switch

### 0.0.4b8
- Initial support for thermostat related quirks concerning non-thermostat devices
  - This does not add support for the thermostat, only allows exporting and controlling non-thermostat devices that are affected by a thermostat related quirk in their raw deviceID 

### 0.0.4b7
- Fix multi endpoint device export logic; pydantic dataclass was exported as yaml rather than the expected format

### 0.0.4b6
- DNS redirection guide for TP-Link Omada Controller, thanks [dnguyen800](https://github.com/dnguyen800)
- [Fan controllers moved from a 0-255](https://github.com/baudneo/cync-lan/commit/6a66d5557066bf4ff9acc0f1abcc6b13e1bf21fb) binary range to using a percentage style 0-100. Thanks @[tobyroworth](https://github.com/baudneo/cync-lan/commits?author=tobyroworth)
  - It's possible that anyone on older firmware may see issues, open an issue and we can try a firmware filter

- Added logic for older (abandoned?) GE Sol lamp device. Thanks @[lukabratzee](https://github.com/Lukabratzee)
  - [This PR contains code written by Claude sonnet](https://github.com/baudneo/cync-lan/pull/20). I am not levying any sort of opinion, just being transparent as people seem to want disclosure of this sort of thing
  - This may also allow backwards compatibility for anyone using old firmware devices


### 0.0.4b5
- Add deviceType 72: Full Color Dynamic Effects Premium Light Strip (16 / 32ft)
  - I don't own any dynamic lights, so I would need debug data from someone to get the dynamic effects / per bulb/ segment state working
  - There is a MITM proxy in the works to facilitate binary debugging for new functionality from unseen devices.
  
### 0.0.4b4
- Add deviceType 76: Full color dynamic cafe lights (24 / 48ft)
  

### 0.0.4b3
- Add better logging for unknown deviceTypes; devices CyncLAN has not seen before.
  - Before, things would 'just work' by brute force for unknown deviceTypes. Now, due to better class handling, we need \
  to know if the device is a light or a switch/plug/fan controller/thermostat/multi-endpoint device for things to work \
  as expected.
  - This requires adding unknown/unseen device `type` to the `device_info` hash map, devices that worked before 0.0.4 \
  **MAY NOT** work in > 0.0.4.
  - This is an easy fix, open an issue with the log line that has the device `type` number and a brief explanation of \
  what the device is and its capabilities: https://github.com/baudneo/cync-lan/issues/12
  - While I understand that this is annoying, this is better for everyone in the long run and as a perk to you, you \
  will get a better detailed MQTT device info page

### 0.0.4b2
- Fix unassigned var name type: name -> dev_name in config export process

### 0.0.4b1
- The underlying cync-lan lib has been merged into one source. Before, 2 versions were maintained for HASS / regular docker images
- Refactored ID handling logic to allow multiple endpoints per node
- Changed logic to view a physical Cync device as a `node` and endpoints as logical representations of the device state; allows multi-endpoint per node logic cleanly
- Aggressive online/offline handling causing false positive offline devices has been turned off while I investigate 
- [Ad-Guard DNS guide](https://github.com/baudneo/cync-lan/blob/python/docs/DNS.md#adguard-home) added to cync-lan DNS docs, thanks @[lbrpdx](https://github.com/baudneo/cync-lan/commits?author=lbrpdx)
- Add device types: 9, 47, 51, 67, 71 and 107
  - Support for device type: 67 -> Outdoor Dual Outlet Plug; required logic change to Node and endpoints
- MeshInfo response pagination fix; some devices/nodes stream the MeshInfo response over multiple packets, some devices send it all in one packet
- Refactoring and better online/offline handling ongoing
  - You may notice differences from < 0.0.4, please open issues for missing/broken/incomplete functionality 

### 0.0.3.b12
- Last checkpoint before merging underlying cync-lan libraries into one.

### 0.0.3b11
- make sure the cync_mesh.yaml exported file is overwritten by default in the HASS image

### 0.0.3b10
- Host tailwinds, animate.css and prism.js locally, removing the internet requirement to render the export page

### 0.0.3b9
- add `raw_debug` options to config. When enabled, will output binary data to/from TCP devices in the logs (VERY verbose)
- fix device type 107: switch to light

### 0.0.3b8
- typo fix: `model-info` to `model_info`: GH mobile editor strikes again

### 0.0.3b7
- add device types 47, 71 and 107

### 0.0.3b6
- fix `functools.partial` error

### 0.0.3b5
- split switch and plug logic

### 0.0.3b4
- fix brightness scale (was 0-255, we want 0-100)
- add device types 39, 57
  - 39: direct connect on/off paddle switch - neutral required
  - 57: direct connect on/off paddle switch - NO neutral required (same model # with a NN appended for No Neutral, good to know)
- Updated Github notifications so I actually receive notifications of issues/PR's, etc.
  - Sorry, I've been a bit stunned lately. Hopefully this is the last beta release before 0.0.3

### 0.0.3b3
- Fix new logic in 'supported_color_modes'
- Add device types: 17, 18 
  - 2700K dimmable bulbs [CLED199L2]
  - 18 may be the same bulb with a newer SoC

### 0.0.3b2
- Nothing of note

### 0.0.3b1
- Add default 'supported_color_modes' of 'brightness' in device / entity registration messages to stop throwing deprecation warnings (2025.3)
- Changed 'object_id' to 'default_entity_id' in device / entity registration messages ('object_id' retained for older versions)
- Add random delay (5-15s) after hass birth message before re-announcing device config and state.
  - Fixes no devices after a hass restart
- bumped min ver to get the fix out

### 0.0.2b2
- Add restart button to export: unhidden after receiving success from submitting OTP button
- Fix cached token reading: attempted to read a binary file in text mode
- Fix device closing logic: expected exception is now `pass`ed, proper input to asyncio.wait()
- Optimizations

### 0.0.2b1
- Add "state" key and value when updating brightness, temp or RGB. Even though hass docs say it is optional, HASS logs shows exceptions when this is omitted due to using direct access to a dict key: variable_dict["key"] instead of checking for key existence or using .get().

### 0.0.2a1
- Rough in fan support (WIP)
- optimizations

## 0.0.1
- Initial release