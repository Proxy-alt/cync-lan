# Inline annotations

The `[cync-lan reverse-engineering note ...]` comments added to the decompiled
tree, extracted so they survive without shipping the tree itself.

Reproduced as a list rather than a patch on purpose: a patch's context lines
contain jadx-generated identifiers, and those are **not stable across runs**
(see ../README.md). A patch would fail to apply; a file path plus the note text
does not.


## `chip/devicecontroller/ChipDeviceController.java`

```java
 * [cync-lan reverse-engineering note - see /Users/proxy-alt/Downloads/cync_decompiled_v2/sources/chip/CYNC_LAN_FINDINGS.md]
 * Confirmed genuine: this is Google's Matter (Project CHIP) Android SDK (System.loadLibrary("CHIPController"),
 * standard commissionDevice/pairDevice/openPairingWindow API surface), not a coincidentally-named library.
 * However it is dead weight for Cync's own product surface: the only real caller of ChipDeviceController
 * anywhere in the decompiled app is Tuya's bundled Matter SDK (com.thingclips.sdk.matter*, e.g.
 * com/thingclips/sdk/matterlib/pbddddb.java:116/280), which itself has zero reachable callers from
 * com.gelighting.* or com.savantsystems.oneapp.* - every apparent Cync/Savant reference into the chip.*
 * namespace traced back to either an R8 class-merging coincidence (unrelated synthetic lambda classes
 * sharing this package) or a borrowed numeric/string constant, never an actual commissioning call. No
 * live Matter provisioning flow for Cync hardware was found; see the findings file for full citations.
 */
```


## `com/gelighting/cbygekit/foundation/Utilities$chunkByteArray$1.java`

```java
 * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
 * The chunking helper passed to CollectionsKt.chunked() in Utilities.m13402k (SetWifiCommand's WiFi
 * credential payload builder). Each invocation prefixes its 8-byte-or-fewer chunk with a running
 * 1-based index byte (Ref.IntRef starts at 0, pre-incremented before use -> first chunk gets index 1,
 * matching Utilities$chunkByteArray$1.java's role in the doc's SetWifiCommand chunk/packet mapping).
 * Confidence: High confidence, decompile-only but a direct, unambiguous read of straightforward code.
 */
```


## `com/gelighting/cbygekit/foundation/Utilities.java`

```java
     * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
     * Builds SetWifiCommand's plaintext payload before chunking:
     *   [totalChunks(1B) = ceil((len(ssid)+len(pass)+5)/8)][len(ssid)(1B)][ssid UTF-8]
     *   [len(pass)(1B)][pass UTF-8][0x01][deviceType.id(1B) - the trailing `i` param]
     * then splits it into 8-byte chunks via CollectionsKt.chunked(..., 8,
     * Utilities$chunkByteArray$1), which prefixes each chunk with a 1-based running index byte - see
     * that class's own note. Called from SetWifiCommand.mo14060M, iterated once per chunk.
     * Confidence: High confidence, decompile-only - the one piece of this protocol with no prior-art cross-validation available (see SetWifiCommand.java's note).
     */
```


## `com/gelighting/cbygekit/foundation/commands/Telink.java`

```java
 * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
 * Central registry of the Telink Mesh BLE GATT UUIDs and shared crypto primitives used to
 * provision/pair a device. See the field-level notes below (f28867a..f28880n) for each UUID's
 * confirmed role, and m13403a/m13404b/m13406d for the XOR/AES-ECB-byte-reversal/pad16 primitives
 * used throughout the pairing handshake and command encryption.
 * The v2 decompile's Kotlin `Intrinsics.checkNotNullExpressionValue(..., "<get-XXX_UUID>(...)")`
 * calls in TelinkDeviceBleManager.mo14330f (services/devices/telink/TelinkDeviceBleManager.java
 * ~line 1616-1638) recover the *original* Kotlin property names for every UUID constant here -
 * TELINKSERVICE_UUID, TELINKPAIRCHAR_UUID, TELINKSTATUSCHAR_UUID, TELINKCOMMANDCHAR_UUID,
 * TELINKOTACHAR_UUID, DEVICEINFORMATIONSERVICE_UUID, FIRMWARECHARACTERISTIC_UUID - independent,
 * high-confidence confirmation of every UUID role the doc already established.
 * Confidence: Highest confidence - validated against real, working, cloned code (python-dimond) for the four core Telink UUIDs; the DIS/Firmware pair is standard BLE SIG, not Telink-specific.
 */
```

```java
     * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
     * A near-mirror (byte-reversed) of the primary Telink service UUID f28872f. NEW FINDING beyond
     * the doc (which flagged this pair as "purpose not confirmed"): TelinkDeviceBleManager.mo14329e
     * (services/devices/telink/TelinkDeviceBleManager.java ~line 1596-1608), which overrides Nordic
     * BleManager's required-service gate, looks up gatt.getService(f28869c) then
     * .getCharacteristic(f28871e) and returns false (rejecting the device) if either is missing.
     * So this pair functions as the manager's "is this actually a Telink Mesh device I can talk to"
     * sentinel check - run before the main GATT characteristic bind in mo14330f below - not part of
     * the pairing/command protocol itself.
     * Confidence: High confidence, decompile-only but now traced to a concrete call site (was previously "purpose not determined").
     */
```

```java
     * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
     * Characteristic under the byte-reversed service f28869c - see the note on that field above for
     * the NEW FINDING that this pair gates TelinkDeviceBleManager.mo14329e's
     * isRequiredServiceSupported()-style check, not a standard SIG UUID.
     * Confidence: High confidence, decompile-only but now traced to a concrete call site (was previously "purpose not determined").
     */
```

```java
     * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
     * Primary Telink Mesh GATT service. Kotlin getter name "TELINKSERVICE_UUID" recovered from
     * TelinkDeviceBleManager.mo14330f's Intrinsics debug string. Byte-for-byte identical to
     * python-dimond's hardcoded service UUID (dimond/__init__.py:114-116) - independent 2018
     * reverse-engineering project converging on the same value, strong evidence this is generic
     * Telink Mesh SDK behavior rather than Cync-specific or a decompile artifact.
     * Confidence: Highest confidence - validated against real, working, cloned code (python-dimond).
     */
```

```java
     * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
     * Status characteristic ("TELINKSTATUSCHAR_UUID") - subscribed for notify at GATT-discovery time
     * in TelinkDeviceBleManager.mo14330f. Carries all device->app notifications: mesh status, the
     * 8-byte leading NotificationType byte, WiFi sub-protocol notifications, etc. Matches
     * python-dimond's hardcoded "...1911" UUID exactly.
     * Confidence: Highest confidence - validated against real, working, cloned code (python-dimond).
     */
```

```java
     * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
     * Command characteristic ("TELINKCOMMANDCHAR_UUID") - the write path for every encrypted mesh
     * command (see TelinkDeviceBleManager.m14326L / "writeCommand", ~line 1229), including the
     * chunked SetWifiCommand packets. Matches python-dimond's "...1912" (control/command write) UUID.
     * Confidence: Highest confidence - validated against real, working, cloned code (python-dimond).
     */
```

```java
     * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
     * Pairing characteristic ("TELINKPAIRCHAR_UUID") - the auth handshake / session-key derivation /
     * mesh-credential-handoff characteristic. Both the initial connect-time pairing write
     * (TelinkDeviceBleManager.m14334v, "authenticate") and the post-pairing mesh-name/password/LTK
     * handoff (TelinkDeviceBleManager$pairMesh$2.java) write here. Matches python-dimond's "...1914"
     * (pairing) UUID exactly.
     * Confidence: Highest confidence - validated against real, working, cloned code (python-dimond).
     */
```

```java
     * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
     * NEW FINDING beyond the doc: this is NOT a placeholder overwritten with SecureRandom output -
     * it is `final` (assigned once, in the static initializer below, to
     * {0xA0,0xA1,0xA2,0xA3,0xA4,0xA5,0xA6,0xA7,0,0,0,0,0,0,0,0}) and is used AS-IS, unmodified, at
     * two independent real call sites: TelinkDeviceBleManager.m14334v ("authenticate", writes
     * f28877k[0:8] as the "R_app" 8 bytes to the pairing characteristic) and
     * TelinkDeviceBleManager$..d (C2184d.mo14353a, the read-response callback, which reconstructs
     * R_app from f28877k[0:8] again to derive the session key). Conclusion: the real Cync/GE app,
     * unlike python-dimond (which generates 8 fresh SecureRandom bytes per session), hardcodes its
     * "R_app" contribution to a fixed constant every single pairing attempt. This resolves the doc's
     * open caveat about f28877k's role.
     * Confidence: High confidence, decompile-only but cross-checked across two independent call sites in this v2 tree.
     */
```

```java
     * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
     * A fully pre-baked 17-byte pairing-characteristic write: [0x0C] + f28877k[0:8] (the fixed
     * "R_app" bytes above) + 8 precomputed bytes = key_encrypt("telink_mesh1", "123", key=R_app)[0:8].
     * Used verbatim by TelinkDeviceBleManager.m14334v when the target mesh name/password are exactly
     * the Telink factory-default ("telink_mesh1"/"123", see com.thingclips.sdk.bluetooth.dqdpbbd.
     * qpppdqb) - i.e. this is the literal bytes written when authenticating against a brand-new,
     * never-provisioned device before it has been assigned custom mesh credentials.
     * Confidence: High confidence, decompile-only, directly traced to TelinkDeviceBleManager.m14334v.
     */
```

```java
     * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
     * PARTIAL NEW FINDING beyond the doc's "genuinely open" item on this enum: wire writes use
     * literal integers, not this enum, but TelinkDeviceBleManager$pairMesh$2.java (the mesh-credential
     * handoff, ~lines 236-241) writes literal opcode bytes 4, 5, 6 for the encrypted
     * NAME / PASSWORD / LTK packets respectively - which line up with this enum's ordinals 3
     * (PAIR_NETWORK_NAME), 4 (PAIR_PASS), 5 (PAIR_LTK) shifted by exactly +1 (literal = ordinal + 1).
     * Separately, TelinkDeviceBleManager.m14334v's initial pairing write uses literal opcode 0x0C
     * (12) - which under the same ordinal+1 hypothesis would be ordinal 11 = ENC_REQ, a semantically
     * plausible match for "the opening handshake request". Only 4 of the 14 ordinals have a
     * confirmed literal mapping this pass (3,4,5,11) - not exhaustively proven for the rest of the
     * enum, and the 0x0C literal is coincidentally read via an unrelated MQTT constant
     * (MqttWireMessage.MESSAGE_TYPE_PINGREQ == 12) by JADX/the obfuscator, which is a decompiler
     * naming artifact, not a real MQTT reference - don't be misled by that import.
     * Confidence: Lower confidence / genuinely open - a plausible partial resolution (ordinal+1), not conclusively proven for the full enum.
     */
```

```java
     * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
     * XOR(pad16(a), pad16(b)) - this is exactly python-dimond's key-derivation building block: the
     * mesh session key material is XOR(meshName, meshPass), and this same helper builds
     * key_encrypt's plaintext XOR(name,password) too. Both inputs are right-padded to 16 bytes with
     * zero (via m13406d below) before XORing.
     * Confidence: Highest confidence - validated against real, working, cloned code (python-dimond).
     */
```

```java
     * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
     * THE Telink SDK byte-reversal quirk: reverses the key, reverses the plaintext, runs plain
     * AES/ECB/NoPadding (Cipher.ENCRYPT_MODE=1), then reverses the ciphertext back before returning.
     * Exactly matches python-dimond's `encrypt()` (dimond/__init__.py:29-35):
     * `AES.new(bytes(reversed(key)), MODE_ECB).encrypt(bytes(reversed(data)))` then reverse the
     * result. Load-bearing real Telink SDK behavior, not a decompiler artifact - used for every
     * AES-ECB operation in this codebase: key_encrypt, session-key derivation (generate_sk), and the
     * command MIC/keystream (both single-block AES-ECB ops per docs/ble_provisioning_protocol.md's
     * "Command encryption" section).
     * Confidence: Highest confidence - validated against real, working, cloned code (python-dimond).
     */
```

```java
     * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
     * Parses a "AA:BB:CC:DD:EE:FF"-style MAC string, takes the last `i` bytes, and byte-reverses
     * them. This is the "macdata" (reversed device MAC) used as auth_nonce/iv key material in the
     * MIC + keystream command-encryption algorithm - see TelinkDeviceBleManager.m14326L
     * (writeCommand, ~line 1413: `Telink.m13405c(4, mac2)`), matching python-dimond's
     * "byte-reversed once at object construction" macdata (dimond/__init__.py:107).
     * Confidence: Highest confidence - validated against real, working, cloned code (python-dimond).
     */
```

```java
     * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
     * "pad16": right-pads (or truncates) startArray to length `i` using fill byte `b` (always 0x00
     * at every call site seen this pass). Equivalent to python-dimond's pad16() used before every
     * AES-ECB call in the pairing handshake (mesh name/password/LTK padding, R_app/R_dev padding).
     * Confidence: Highest confidence - validated against real, working, cloned code (python-dimond).
     */
```


## `com/gelighting/cbygekit/foundation/wifi/XlinkAgentManager.java`

```java
 * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
 * Peripheral to BLE provisioning: wraps io.xlink.wifi.sdk.XlinkAgent, Telink/Cync's cloud-relay
 * "XLink" SDK used once a device is WiFi-connected and needs to reach Cync's cloud (the
 * api2.xlink.cn / corp_id 1007d2ad150c4000 endpoints python-laurel also targets). Handles
 * login/session management with that cloud service - not BLE, not part of the local
 * pairing/WiFi-handoff protocol documented here.
 * Confidence: Not covered by the BLE provisioning doc's research pass - role inferred from class/method names and imports.
 */
```


## `com/gelighting/cbygekit/product/ProductModel.java`

```java
         * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo,
         * "Full light-run-mode incl. MultiColor/MusicShow" section, Reveal follow-up]
         * Reveal-branded SKUs (this one and the "RevealFullColor*"/"RevealSoftWhite*" entries
         * below) are what determine whether a bulb is a "Full Color" or "Soft White" Reveal
         * variant - purely a matter of which SKU/product model the physical bulb is, not a wire
         * payload field. The actual Reveal wire command (SetLightRunModeCommand modeCode=3, or
         * the redundant SetComboCommand + RevealColor path) is identical regardless of which
         * Reveal SKU the target device is.
         * Confidence: confirmed via decompiled source.
         */
```


## `com/gelighting/cbygekit/services/commission/BleDeviceCommissionService.java`

```java
     * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo,
     * "Operational prerequisite: motion sensors must be woken before settings/schedule writes"]
     * WARNING - FALSE LEAD, do not re-investigate: despite the name "checkMotionSensorOperation"
     * (obfuscated to `E`/`m13698E` - confirmed via the sibling
     * BleDeviceCommissionService$checkMotionSensorOperation$1 continuation class), this method has
     * nothing to do with the wake-up/discoverability gate. It runs during initial multi-device
     * commissioning and re-homes a sensor's group/subgroup mapping (see the
     * WhereToAddDevice.Group/Subgroup handling a few hundred lines below this method, and
     * DevicePath$Group/DevicePath$Subgroup construction) - i.e. "smart operation" group
     * assignment at first-time setup, not an online/discoverability check. The real wake-gate logic
     * is MotionSensorServiceDefault.isOnline()/m14739t() (see that file) plus
     * DeviceSettingsWakeUpFragment's reactive ConnectionState watch - unrelated to this class.
     * Confidence: confirmed via decompiled source.
     */
```


## `com/gelighting/cbygekit/services/devices/BleDeviceScanner.java`

```java
 * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
 * New-device discovery - the step before any GATT/pairing work above. NOT an OS-level BLE scan
 * filter: BleDeviceScanner$findDevices$1.java:360 builds an empty `new ScanFilter.Builder().build()`,
 * so BluetoothLeScanner.startScan sees every nearby BLE advertisement. All real filtering happens in
 * application code afterward, in m13735b/parseScanResult (this file):
 *  1. Reads BLE manufacturer-specific data keyed by company ID 0x0211 - confirmed here as
 *     f31961i, built at static-init time from bytes {17,2} (little-endian 17 + 2*256 = 0x0211) -
 *     Telink Semiconductor's registered Bluetooth SIG company ID, and the same 0x11,0x02 vendor-ID
 *     bytes that prefix every mesh command payload.
 *  2. Within that payload, reads a device-type byte, validated against DeviceType.java's full
 *     GE/Cync product catalog - rejected outright ("Unknown device type") if unrecognized.
 *  3. Separately checks the advertised local BLE name against "telink_mesh1" (~line 457) - Telink's
 *     stock factory-default/unprovisioned-node name - to set DeviceScanRecord.isNewDevice true.
 * Practical takeaway for a from-scratch client: no special BLE scan filter needed - filter
 * client-side on manufacturer-data company ID 0x0211, parse the device-type byte, and treat the
 * advertised name "telink_mesh1" as the "ready to pair" signal.
 * Confidence: High confidence, decompile-only but fully traced end-to-end and internally consistent (2026-07-17 pass).
 */
```


## `com/gelighting/cbygekit/services/devices/DeviceManager.java`

```java
     * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo,
     * "Operational prerequisite: motion sensors must be woken before settings/schedule writes"]
     * Verified against this current decompile: `i()` (obfuscated to `mo13807i`) is the single
     * per-device StateFlow<AvailabilityState> every device type reads to know online/offline
     * status. MotionSensorServiceDefault.m14739t() (isOnline()) calls exactly this method and
     * checks `instanceof AvailabilityState.Online` - there is no separate/dedicated
     * "discoverable" or BLE-scan API anywhere in DeviceManager.
     * Confidence: confirmed via decompiled source.
     */
```


## `com/gelighting/cbygekit/services/devices/DeviceWifiConnectionManager.java`

```java
 * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
 * The app-level orchestrator for WiFi handoff. m13937d ("setWifi", ~line 812) constructs a
 * `new SetWifiCommand(ssid, key, deviceType)` and dispatches it via
 * `deviceController.mo14149i(setWifiCommand, null, ...)` at two call sites (~lines 891 and 1016 in
 * this v2 decompile - same line numbers the doc cites) - the `null` target address is what
 * TelinkBleDeviceController.m14192x resolves to the SELF_ADDRESS sentinel MeshAddress(0) for an
 * unprovisioned device. This is the confirmed entry point every other command also goes through
 * (deviceController.mo14149i), so SetWifiCommand rides the exact same encrypted mesh-command
 * pipeline as ordinary commands - no special unencrypted framing.
 * Confidence: Highest confidence - the dispatch entry point and call-site line numbers are directly re-verified in this v2 tree against the doc's citations.
 */
```


## `com/gelighting/cbygekit/services/devices/WifiHubProxyManager.java`

```java
 * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
 * Peripheral to BLE provisioning: manages routing mesh commands through an already-WiFi-provisioned
 * "hub" device (a device that stays connected to the mesh over WiFi and relays to other devices)
 * rather than direct BLE. Relevant only after a device has already been through the BLE
 * pairing/WiFi-handoff flow documented on TelinkDeviceBleManager/SetWifiCommand - not part of the
 * provisioning handshake itself.
 * Confidence: Not covered by the BLE provisioning doc's research pass - role inferred from class/method names and imports.
 */
```


## `com/gelighting/cbygekit/services/devices/WifiPeerProxyManager.java`

```java
 * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
 * Peripheral to BLE provisioning: sibling of WifiHubProxyManager, manages routing mesh commands
 * through WiFi-connected "peer" devices (candidate selection/scoring, e.g. activateBestCandidates)
 * rather than a single designated hub. Relevant only after devices are already WiFi-provisioned -
 * not part of the BLE pairing/WiFi-handoff flow itself.
 * Confidence: Not covered by the BLE provisioning doc's research pass - role inferred from class/method names and imports.
 */
```


## `com/gelighting/cbygekit/services/devices/command/AddDeviceSceneCommand.java`

```java
 * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo]
 * Programs a device's on-device scene-slot state (called once when a scene, or the implicit
 * per-device scene backing a simple Schedule, is created/edited - NOT at trigger time; see
 * ExecuteSceneCommand for the trigger-time command). Two payload shapes depending on device
 * routing, both in mo14013g below:
 *  - Non-hub-routed devices: this class's own opcode array (f34358t, {0xEE,0x11,0x02}) is NOT
 *    the real outer op_code - dispatched via XlinkCommandDelegate.DefaultImpls.c(...) ->
 *    CommandDelegate.h(), which hardcodes the real outer op_code to 0x8E, exactly like the
 *    already-confirmed siblings (indicator LED / motion settings / motion schedule). Full
 *    payload (m14018x()): [0xEE,0x11,0x02] + [actionTypeByte, sceneNum] + [mode, param,
 *    colorType, R, G, B] (a 6-byte per-device state block) + [fadeByte, 0xFF] - 13 bytes.
 *  - Hub-routed devices: a structurally DIFFERENT payload - a manually-built WriteBuffer frame
 *    with FrameCode headers + an explicit little-endian MeshAddress target, dispatched via the
 *    raw pre-framed xlinkCommandDelegate.mo14053e() (the "e()" method - same dispatch class as
 *    the pure Hub Scenes/Schedules commands elsewhere in the mesh_opcodes.md doc), not through
 *    the 0x8E-bug path at all.
 * The fade byte (f34361p, ScheduleFade: NO_FADE=0xFF, FADE_10_SECONDS=1, FADE_30_SECONDS=2,
 * FADE_1_MINUTE=3, FADE_5_MINUTES=4, FADE_10_MINUTES=5, FADE_20_MINUTES=6, FADE_30_MINUTES=7)
 * is a coded duration bucket, not raw seconds - confirmed hardware-side (the bulb's own
 * firmware executes the fade autonomously from this byte at scene-programming time;
 * ExecuteSceneCommand never resends color/fade data at trigger time).
 * Not yet wired into cync-lan's devices.py (would need the 0x8E fix applied from scratch).
 * Confidence: opcode array / 0x8E dispatch path (non-hub) confirmed via decompiled source;
 * hub-routed WriteBuffer path confirmed via decompiled source but structurally unconfirmed
 * against a live capture
 */
```

```java
     * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo]
     * f34358t (OPCODE_BYTES) = {0xEE,0x11,0x02}. NOT the real outer op_code for the non-hub
     * send path (mo14013g below) - it's the leading bytes of the payload sent under the real
     * outer op_code 0x8E, via CommandDelegate.h(). See class-level note above.
     * Confidence: confirmed via decompiled source; op_code=0x8E confirmed by analogy to the
     * indicator-LED sibling command's real-hardware test
     */
```


## `com/gelighting/cbygekit/services/devices/command/ControlDeviceGroupCommand.java`

```java
 * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo]
 * NOT what the class name suggests: this is mesh GROUP-MEMBERSHIP management (a device
 * subscribing/unsubscribing to a group's pub/sub address, via Action ADD/REMOVE), not "control
 * an entire group's state in one packet." Base class for AddDeviceGroupCommand/
 * RemoveDeviceGroupCommand. Payload (m14022x()): action byte (ADD=1/REMOVE=0) + 2-byte
 * little-endian group address (ExtensionsKt.m13359f) + optional GroupReachFlag byte
 * (RX=0x87, RXTX=0x00). No other occurrence of opcode 0xD7 anywhere in com/gelighting/cbygekit
 * (per the doc's grep). This IS the trustworthy path in the doc's headline sense: the opcode
 * array f34364s = {0xD7,0x11,0x02} genuinely is the real outer op_code for the hub-relayed
 * branch of mo14013g below (mo14054f((byte) -41, ...) - a direct f() call, not routed through
 * CommandDelegate.h()'s 0x8E hardcoding).
 * ADDITIONAL FINDING beyond what mesh_opcodes.md documents: mo14013g here is actually
 * branched - when getProductType().f31219d is FALSE (non-hub-relayed devices), it instead
 * dispatches via XlinkCommandDelegate.DefaultImpls.c(...) -> CommandDelegate.h(), the SAME
 * 0x8E-hardcoding bug path as SetStatusIndicatorSettingsCommand/AddDeviceSceneCommand/etc. -
 * meaning 0xD7 group-membership commands to non-hub-relayed devices may ALSO be sent under a
 * forced outer op_code of 0x8E with {0xD7,0x11,0x02,...} as payload, not confirmed/discussed in
 * the doc's Groups section (which only cites the f()-with-real-0xD7 branch). cync-lan's own
 * set_group_membership() targets an individual device's own address (op_code=0xD7 assumed
 * real, cmd_code=0x0E predicted) - worth revisiting if it doesn't work against a non-hub device.
 * Real group POWER control (controlling a whole group's state at once) is a separate, only
 * plausible, unconfirmed mechanism - see mesh_opcodes.md's Groups section for the MeshAddress
 * group-range/target_id+sub_id addressing discussion.
 * Confidence: op_code=0xD7 / hub-relayed dispatch path confirmed via decompiled source, matches
 * cync-lan's already-implemented set_group_membership(); the non-hub-relayed 0x8E-bug branch
 * noted above is confirmed via decompiled source but not covered/tested per mesh_opcodes.md
 */
```

```java
     * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo]
     * f34364s (OPCODE_BYTES) = {0xD7,0x11,0x02}. Genuinely the real outer op_code (0xD7) for
     * the hub-relayed dispatch branch in mo14013g below - see class-level note above for the
     * additional non-hub-relayed branch that instead routes through the 0x8E-bug path.
     * Confidence: confirmed via decompiled source
     */
```


## `com/gelighting/cbygekit/services/devices/command/CreateSceneHubCommand.java`

```java
 * [cync-lan reverse-engineering note - see docs/cync_automations.md in the cync-lan repo]
 * Real outer op_code: HUB_CREATE_SCENE = 0x10 (XlinkCommandCode.java). Payload = CreateSceneRequest
 * below: a String30-encoded name (up to 30 bytes) + a 2-byte "iconId" field currently hardcoded 0.
 * Builds its own complete Frame (Frame.Direction.REQ, op HUB_CREATE_SCENE) and calls
 * Frame.m14440a() directly (confirmed in mo14023N below), then dispatches via the raw
 * pre-framed xlinkCommandDelegate.mo14053e() - NOT the normal envelope builder used by everyday
 * light-control commands. Unlike DeleteSceneHubCommand/DeleteScheduleHubCommand/
 * ToggleAutomationHubCommand/CreateScheduleHubCommand (all directly re-verified: they only call
 * XlinkTranslatorKt.m14449a(), never Frame.m14440a()), CreateSceneHubCommand DOES call
 * Frame.m14440a() here - initially flagged as an open question whether this changes the
 * transport picture for Create commands specifically. RESOLVED (see Frame.java/Xlink.java): it
 * doesn't. Frame.m14440a() builds the EXACT SAME frame shape as Xlink.m14391a() - msgId(4B LE) +
 * flag(1B) + op_code(1B) + len(2B LE) + payload + checksum(1B, sum-mod-256 over
 * op_code+len+payload only), 0x7E-delimited and 0x7D/0x7E byte-stuffed (identical escape rule:
 * 0x7E->{0x7D,0x5E}, 0x7D->{0x7D,0x5D}). The only structural difference is that Frame generalizes
 * Xlink's hardcoded 0xF8 marker byte into a Direction enum (REQ=0xF8, RSP=0xF9, ANNOUNCE=0xFA),
 * and this command uses Direction.REQ - whose byte value (0xF8) is IDENTICAL to Xlink.m14391a()'s
 * hardcoded marker. So for this call site, Frame.m14440a() and Xlink.m14391a() emit
 * byte-identical output: this is the same legacy HDLC/PPP pathway reached via a different encoder
 * class, not a different or newer transport.
 * Frame.m14440a() as an encoder is narrowly used (only here and in CreateGroupHubCommand /
 * HUB_CREATE_GROUP) - same rarity tier as the XlinkTranslatorKt-based commands. But the frame
 * SHAPE itself is not niche: XlinkNotificationParser.m14437a() decodes this exact layout
 * (0x7E...0x7E, msgId@[1:4], flag@[5] matching Direction's byte values, op_code@[6], len@[7:8],
 * payload, checksum) for ALL incoming StatusNotifications app-wide, and per the pre-existing note
 * on XlinkDeviceManager.java's CommandDelegate.g()/mo14055g, this same Xlink.m14391a() framing is
 * also what the "trustworthy" f()/g() envelope path funnels ordinary/everyday command bytes
 * through right before dispatch. That raises confidence the framing is genuinely live/exercised
 * code, not dead or BLE-only - but does NOT independently confirm it rides the same TCP socket
 * cync-lan's relay intercepts, since mo14053e()'s actual network-write/postCommand path was not
 * traced here. XlinkTranslatorKt.m14449a()/Frame.m14440a() -> Xlink.m14391a() remain structurally
 * unlike cync-lan's own confirmed TCP wire format (no delimiters/escaping there; different msgId
 * width; no embedded length field; extra routing sub-fields). Whether this raw frame rides over
 * the same TCP relay cync-lan intercepts, or is BLE-GATT-specific, is still UNCONFIRMED.
 * Net effect: this does NOT change the transport-risk conclusion in cync-lan's favor - treat
 * CreateSceneHubCommand's transport confidence as the SAME "plausible, not independently
 * confirmed" tier as Delete/Toggle/CreateSchedule. See cync_automations.md's "HA -> Cync
 * (writing)" section.
 * Confidence: op_code, dispatch path, and the Frame.m14440a()==Xlink.m14391a() byte-shape
 * equivalence are all confirmed via decompiled source; wire-transport (TCP-relay vs BLE-GATT)
 * question explicitly still unresolved.
 */
```


## `com/gelighting/cbygekit/services/devices/command/CreateScheduleHubCommand.java`

```java
 * [cync-lan reverse-engineering note - see docs/cync_automations.md in the cync-lan repo]
 * Real outer op_code: HUB_CREATE_SCHEDULE = 0x92, confirmed here as (byte) -110 (mo14023N below).
 * Payload (WriteBuffer(50), fields written in this order): sceneId as 4-byte LE (m14443c) at
 * offset 0-3 - a Schedule fires an existing Scene by ID, matching ScheduleModel's sceneIdValue
 * field - then the write cursor jumps to offset 30 (writeBuffer.f38053b = 30, leaving a 26-byte
 * zero gap at offset 4-29), a zero uint16 (offset 30-31), the enabled flag as 1 byte (offset 32),
 * one zero byte (offset 33), and 16 zero-padding bytes (offset 34-49) = 50 bytes total. This is
 * structurally the same tail shape as ToggleAutomationHubCommand's 52-byte payload (26 zero
 * bytes + zero u16 + enabled + zero byte + 16 zero bytes) minus a leading scheduleId, which makes
 * sense since no scheduleId exists yet at creation time. Notably NO String30 name field or
 * day/time-trigger fields appear in this payload at all - cync_automations.md flagged
 * "String30 name encoding, full schedule field layout" as still-needed research; this payload
 * dump doesn't show where/if that data is sent (possibly a separate command not yet identified).
 * Builds its own complete wire frame via XlinkTranslatorKt.m14449a() and dispatches through the
 * raw pre-framed xlinkCommandDelegate.mo14053e(), bypassing the normal envelope builder.
 * UNCONFIRMED, genuinely open question (same as the other Hub commands in this family): whether
 * this raw HDLC-style frame (XlinkTranslatorKt.m14449a() -> Xlink.m14391a(), 0x7E-delimited,
 * byte-stuffed - structurally unlike cync-lan's own confirmed TCP wire format) rides over the
 * same TCP relay cync-lan intercepts, or is BLE-GATT-specific. Not yet wired into devices.py.
 *
 * UPDATE (Frame.m14440a() cross-check, prompted by CreateSceneHubCommand's sibling finding): this
 * class was directly re-verified and does NOT call Frame.m14440a() - only XlinkTranslatorKt.m14449a(),
 * same as Delete/ToggleAutomationHubCommand. (CreateSceneHubCommand is the only Hub command found
 * that calls Frame.m14440a() directly, alongside CreateGroupHubCommand for HUB_CREATE_GROUP.) Moot
 * either way: Frame.m14440a() was traced and found to build the exact same frame shape as
 * Xlink.m14391a() (same field layout, same 0x7E-delimit/byte-stuff scheme; the REQ direction byte
 * CreateSceneHubCommand uses is literally the same 0xF8 value Xlink.m14391a() hardcodes) - it's the
 * identical legacy HDLC/PPP pathway via a different encoder class, not a different transport. So this
 * class's TCP-vs-BLE transport confidence is unchanged and matches CreateSceneHubCommand's exactly.
 *
 * UPDATE (follow-up pass, resolves the "where's the name/trigger-time/day-of-week data" question
 * flagged above): it's option (a) from that question - a SEPARATE wire command, sent right after
 * this one, carries it. Traced end-to-end: RoutinesService.m14800Q() ["getNextScheduleId"],
 * RoutinesService.java:1518-1671, is what actually constructs and dispatches
 * CreateScheduleHubCommand (see :1647) - its whole purpose is to ask the Hub to allocate a fresh
 * scheduleId (returned via HubCreateScheduleNotification, unwrapped at :1589/:1660). This command
 * really is just an ID-allocator + bare placeholder (sceneId + enabled), confirmed correct as
 * analyzed - the missing metadata is NOT hidden in this payload.
 *
 * The real metadata carrier is RoutinesService.m14788D() ["addScheduleToDevices"],
 * RoutinesService.java:409-551 - called by ScheduleServiceDefault's createSceneSchedule/
 * createDevicesSchedule flows (e.g. ScheduleServiceDefault.java:463, :773, :796 etc., all via the
 * shared m14788D helper) once a ScheduleModel with the newly-allocated ID has been built. It picks
 * one of two commands depending on whether the location has a Hub:
 *   - AddAutomationHubCommand (Hub/xlink path, RoutinesService.java:496; class at
 *     services/devices/command/AddAutomationHubCommand.java) - outer op_code (byte) -107 = 0x95,
 *     an 11-byte WriteBuffer payload: scheduleId (u16, mo14013g:115), sceneId (u16, :117), a
 *     day-of-week bitmask packed Sun=0x01..Sat=0x40 (1 byte, :118-145, from ScheduleModel.f41886f
 *     Set<ScheduleDay>), then either an epoch-second value for ScheduleTime.Local (LocalDateTime
 *     built against a fixed dummy date so this really only encodes seconds-since-midnight, not a
 *     real date - :146-159) or a signed sunrise/sunset offset byte (0-15/-0x10 sentinel), and
 *     finally sceneId again (u16, :160) - built via the same XlinkTranslatorKt.m14449a() raw-frame
 *     path as this command. Uses XlinkCommandCode -107, NOT the same op_code family prefix as this
 *     command (0x92) - "Schedule" (this class, ID+enabled only) and "Automation" (day/time/scene
 *     trigger) are two DISTINCT wire-level concepts on the Hub, even though the app's UI and cloud
 *     DTO (ScheduleItem/ScheduleTrigger) present them as one merged "Schedule" entity.
 *   - AddAutomationCommand (non-Hub/direct BLE-mesh path, RoutinesService.java:524; class at
 *     services/devices/command/AddAutomationCommand.java) - a fixed 4-byte opcode prefix
 *     {0xE5,0x11,0x02,0x00} (f34344r) + a 9-byte tail (m14016x(): scheduleId-lowbyte, an
 *     enabled|0x80 + base-18 flag byte, 0, day-bitmask (same encoding as above), hour, minute,
 *     second (or sunrise/sunset hour=-15/-16 + signed offset in the minute slot), sceneId-lowbyte,
```


## `com/gelighting/cbygekit/services/devices/command/DeleteSceneHubCommand.java`

```java
 * [cync-lan reverse-engineering note - see docs/cync_automations.md in the cync-lan repo]
 * Real outer op_code: HUB_DELETE_SCENE = 0x1F, confirmed here as (byte) 31 (see mo14013g below).
 * Payload: 2 bytes, sceneId as uint16 LE (WriteBuffer.m14444d) - note this contradicts the
 * existing cync-lan experimental_execute_scene service's 1-byte scene_id (0-255) assumption; for
 * *this* command the same underlying field is written 2-byte-wide.
 * Builds its own complete wire frame via XlinkTranslatorKt.m14449a() (NOT Frame.m14440a() - only
 * the translator-based framer is called here) and dispatches through the raw pre-framed
 * xlinkCommandDelegate.mo14053e(), bypassing the normal envelope builder entirely. No MeshAddress
 * is used in the payload (accepted as a parameter, unused in the body) - consistent with being
 * hub-scoped/home-wide, not per-device.
 * UNCONFIRMED, genuinely open question: XlinkTranslatorKt.m14449a() -> Xlink.m14391a() builds a
 * PPP/HDLC-style 0x7E-delimited, byte-stuffed frame, structurally unlike cync-lan's own confirmed
 * 5-byte-header TCP wire format. Whether this frame rides over the same TCP relay cync-lan
 * intercepts at all, or is BLE-GATT-specific, is not knowable from static source reading alone -
 * cync-lan wired this payload through its own PacketBuilder/TCP envelope as a working hypothesis
 * (cync_lan.experimental_delete_scene), pending real-hardware confirmation.
 * Confidence: op_code/payload/dispatch-path confirmed via decompiled source; wire-transport
 * question explicitly unresolved.
 */
```


## `com/gelighting/cbygekit/services/devices/command/DeleteScheduleHubCommand.java`

```java
 * [cync-lan reverse-engineering note - see docs/cync_automations.md in the cync-lan repo]
 * Real outer op_code: HUB_DELETE_SCHEDULE = 0x94, confirmed here as (byte) -108 (mo14013g below).
 * Payload: 2 bytes, scheduleId as uint16 LE (WriteBuffer.m14444d) - same 2-byte-wide pattern as
 * DeleteSceneHubCommand's sceneId.
 * Builds its own complete wire frame via XlinkTranslatorKt.m14449a() (not Frame.m14440a()) and
 * dispatches through the raw pre-framed xlinkCommandDelegate.mo14053e(), bypassing the normal
 * envelope builder. No MeshAddress used in the payload - hub-scoped, not per-device.
 * UNCONFIRMED, genuinely open question: whether this PPP/HDLC-style 0x7E-delimited,
 * byte-stuffed frame (Xlink.m14391a()) rides over the same TCP relay cync-lan intercepts, or is
 * BLE-GATT-specific. cync-lan wired this payload through its own PacketBuilder/TCP envelope as a
 * working hypothesis (cync_lan.experimental_delete_schedule), pending real-hardware confirmation.
 * Confidence: op_code/payload/dispatch-path confirmed via decompiled source; wire-transport
 * question explicitly unresolved.
 */
```


## `com/gelighting/cbygekit/services/devices/command/ExecuteSceneCommand.java`

```java
 * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo]
 * What a schedule trigger (or manual scene activation) actually fires at runtime - contrast
 * with AddDeviceSceneCommand, which programs the scene's state once at creation/edit time.
 * This class's TELINK_OPCODE_BYTES field (f34485p, {0xEF,0x11,0x02}) is NOT the real outer
 * op_code for the mesh-device xlink send path (mo14013g below) - when
 * DeviceTypeKt.m13628c(deviceType) is true, dispatch goes via
 * XlinkCommandDelegate.DefaultImpls.c(...) -> CommandDelegate.h(), which hardcodes the real
 * outer op_code to 0x8E and treats this array as the first bytes of the payload, same bug
 * class as the other siblings. Payload (m14041x()): [0xEF,0x11,0x02,sceneId(0-255),0x01] -
 * 5 bytes, cync-lan sends this as op_code=0x8E, cmd_code=0x0C (predicted), repeat_op_code=False.
 * Scenes are scoped per-home (Location), not per-group.
 * A SEPARATE, unrelated legacy dispatch path exists in the same mo14013g method: when
 * DeviceTypeKt.m13628c(deviceType) is false (non-mesh device types), it calls
 * xlinkCommandDelegate.mo14055g((byte) 30, ...) directly - passing 0x1E (30) as a genuine
 * op_code straight to g(), NOT through the 0x8E-bug path. This 0x1E value is real but belongs
 * to that distinct non-mesh code path - not confirmed to be cync-lan's cmd_code or relevant to
 * the mesh-command family documented here.
 * Never resends color/fade data for real scenes (confirmed hardware-side: the bulb's own
 * firmware executes any programmed fade autonomously from data written once by
 * AddDeviceSceneCommand at scene-programming time) - only references the SceneId.
 * Wired in as EXPERIMENTAL in cync-lan's devices.py (execute_scene()), the riskiest of the
 * wired-in 0x8E-family commands: NOT yet real-hardware tested after the 0x8E correction (only
 * the indicator-LED sibling command has been tested/confirmed). Two independent guesses
 * compound: cmd_code=0x0C (predicted) and target_id=0x00 (guessed sentinel, unconfirmed either
 * way - the one real 0x8E packet capture available used broadcast 0xFF/0xFF instead).
 * Confidence: opcode array / 0x8E dispatch path confirmed via decompiled source + indirect
 * real-hardware evidence (sibling command); cmd_code=0x0C and target_id=0x00 are both
 * unconfirmed guesses for this specific command
 */
```

```java
     * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo]
     * f34485p (TELINK_OPCODE_BYTES) = {0xEF,0x11,0x02}. NOT the real outer op_code for the
     * xlink/mesh send path (mo14013g below) - it's the leading bytes of the payload sent under
     * the real outer op_code 0x8E via CommandDelegate.h(). See class-level note above.
     * f34486q (COMBO_OPCODE_BYTES) = {0xF0,0x11,0x02} - used only in the Telink (BTLE) command
     * path (mo14012f) via AddDeviceSceneCommand.Companion.m14019a(), unrelated to the xlink
     * 0x8E-bug family; not analyzed further here.
     * Confidence: confirmed via decompiled source; op_code=0x8E confirmed by analogy to the
     * indicator-LED sibling command's real-hardware test
     */
```


## `com/gelighting/cbygekit/services/devices/command/RemoveDeviceSceneCommand.java`

```java
 * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo]
 * Not itself given a dedicated subsection in mesh_opcodes.md, but structurally identical to
 * AddDeviceSceneCommand's non-hub dispatch (same {0xEE,0x11,0x02,...} opcode-array family,
 * same DefaultImpls.c -> h() route) - the analysis below is inferred by direct analogy to that
 * documented sibling, not independently confirmed against a live capture.
 * This class's own opcode array (f34625q, {0xEE,0x11,0x02,0x00}) is NOT the real outer
 * op_code for the non-hub send path in mo14013g below - dispatched via
 * XlinkCommandDelegate.DefaultImpls.c(...) -> CommandDelegate.h(), which hardcodes the real
 * outer op_code to 0x8E, same bug class as AddDeviceSceneCommand/SetStatusIndicatorSettings-
 * Command/SetMotionSensorSettingsCommand/SetMotionSensorScheduleCommand.
 * NOTE the hub-routed branch of mo14013g is different from AddDeviceSceneCommand's hub path:
 * instead of a manually-built WriteBuffer/FrameCode frame via mo14053e(), it calls
 * mo14054f((byte) -18, ...) directly - i.e. for hub-routed devices this command DOES pass
 * 0xEE as a genuine outer op_code through the trustworthy f() envelope builder, not through
 * the 0x8E-hardcoding h() path at all. So the "0x8E bug" here is specific to the non-hub branch.
 * Confidence: dispatch-path split (non-hub vs hub-routed) confirmed via decompiled source;
 * op_code=0x8E for the non-hub branch is plausible by analogy to sibling commands, not
 * independently confirmed against a live capture for this specific command
 */
```

```java
     * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo]
     * f34625q (OPCODE_BYTES) = {0xEE,0x11,0x02,0x00}. NOT the real outer op_code for the
     * non-hub send path (mo14013g below) - it's the leading bytes of the payload sent under the
     * real outer op_code 0x8E via CommandDelegate.h(). See class-level note above; note the
     * hub-routed branch uses 0xEE as a genuine op_code instead, via a different code path.
     * Confidence: plausible, not independently confirmed (by analogy to AddDeviceSceneCommand)
     */
```


## `com/gelighting/cbygekit/services/devices/command/SetComboCommand.java`

```java
 * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo]
 * The everyday brightness/color/RGB command (cync-lan's set_brightness/set_temperature/set_rgb
 * op family). UNLIKE the 0x8E-bug command family (SetStatusIndicatorSettingsCommand etc.),
 * this class's opcode array (f34727t, {0xF0,0x11,0x02}) genuinely IS the real outer op_code -
 * mo14013g (the xlink send path) calls xlinkCommandDelegate.mo14054f((byte) -16, ...) directly
 * (the doc's trustworthy "f()" envelope builder), passing 0xF0 as a real op argument, NOT
 * routed through CommandDelegate.h()'s 0x8E hardcoding. Confirmed: op=0xF0 matches cync-lan's
 * already-confirmed production op_code for set_brightness/set_temperature/set_rgb byte-for-byte
 * (cmd_code=0x10, verified against real socat-MITM packet captures).
 * Special case: RevealColor uses a 2-byte [0xFF,0xF0] color-type sentinel (see m14083y() below)
 * in place of the usual 4-byte CCT [pct,0,0,0] / RGB [0xFE,r,g,b] shape - a second, real, NOT
 * currently wired into cync-lan, code path to trigger the same Reveal effect as the dedicated
 * set_light_effect("reveal") (SetLightRunModeCommand, modeCode 0x03) already implements.
 * SetComboCommand explicitly REJECTS RevealColor for hub-relayed devices ("RevealColor is not
 * supported by hubs" - see the executeXlinkCommandViaHub branches further down this file) -
 * only works over direct XLink mesh.
 * Confidence: confirmed via decompiled source (op_code, dispatch path, Reveal sentinel bytes)
 */
```

```java
     * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo]
     * f34727t (OPCODE_BYTES) = {0xF0,0x11,0x02}. This genuinely IS the real outer op_code
     * (0xF0) for this command - see class-level note above; NOT part of the 0x8E-bug family.
     * Confidence: confirmed via decompiled source, matches cync-lan's already-confirmed
     * production op_code for set_brightness/set_temperature/set_rgb
     */
```

```java
             * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo]
             * The 2-byte [0xFF,0xF0] Reveal color-type sentinel, replacing the usual 4-byte
             * CCT/RGB color-type shape. See class-level note above for context (rejected for
             * hub-relayed devices, not wired into cync-lan, redundant with SetLightRunModeCommand's
             * dedicated Reveal mode).
             * Confidence: confirmed via decompiled source
             */
```


## `com/gelighting/cbygekit/services/devices/command/SetLightRunModeCommand.java`

```java
 * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo]
 * Full light-run-mode selection command, op_code = 0xE2, sub-command 0x07 - covers
 * Static/LightShow/MusicShow/Reveal/MultiColor mode selection in one unified command. This
 * class's opcode array (f34780q, {0xE2,0x11,0x02,0x07}) genuinely IS the real outer op_code -
 * mo14013g calls XlinkCommandDelegate.DefaultImpls.a(...) (m14392a), which forwards directly to
 * mo14054f (the trustworthy f() envelope builder) with (byte) -30 = 0xE2 as a real op argument -
 * NOT routed through CommandDelegate.h()'s 0x8E hardcoding (that's DefaultImpls.c/m14394c,
 * a different DefaultImpls method than what's called here).
 * Payload after [0xE2,0x11,0x02,0x07] (m14089x()): [modeCode, index, randomNonce].
 * Confirmed mode values (LightRunMode.java):
 *   0x00 Static      - index always 0
 *   0x01 LightShow    - index 1-9, 65-67 (factory), 10-32 (custom) - cync-lan's current only mode
 *   0x02 MusicShow    - index 1-8, 65 (factory), 10-32 (custom) - NOT raw audio: device does
 *                       audio-reactivity locally via its own mic, wire command just selects a
 *                       preset index, identical mechanism to LightShow
 *   0x03 Reveal       - index always 0
 *   0x04 MultiColor   - index 1-2 (factory), 3-32 (custom) - *activating* a saved scheme only
 * The third payload byte ("randomNonce") is confirmed genuinely random and unvalidated by the
 * receiving device (Random.nextInt() on every real send, see m14089x() below) - a constant
 * 0x00 is safe for any new preset, no captured value needed.
 * cync-lan: reuses set_lightshow's already-confirmed cmd_code=0x0E (no cmd_code risk here).
 * Confidence: confirmed via decompiled source (op_code, dispatch path, mode table, nonce
 * behavior)
 */
```

```java
     * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo]
     * f34780q (OPCODE_BYTES) = {0xE2,0x11,0x02,0x07}. Genuinely the real outer op_code (0xE2)
     * for this command - see class-level note above; NOT part of the 0x8E-bug family.
     * Confidence: confirmed via decompiled source
     */
```


## `com/gelighting/cbygekit/services/devices/command/SetMotionSensorScheduleCommand.java`

```java
 * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo]
 * Motion-sensor schedule write (one of a group's 4 fixed schedule slots: Morning=0, Daytime=1,
 * Evening=2, Sleep=3). This class's own opcode array (f34829r below, {0xF7,0x11,0x02,0x0B}) is
 * NOT the real outer op_code - mo14013g dispatches via
 * XlinkCommandDelegate.DefaultImpls.c(...), which forwards to CommandDelegate.h(), which
 * hardcodes the real outer op_code to 0x8E and treats this array as the first bytes of the
 * payload instead - the identical bug/fix as the sibling commands
 * SetStatusIndicatorSettingsCommand/SetMotionSensorSettingsCommand.
 * Full payload (m14101x(), resolved to the exact bit level), 13 bytes total:
 * [0xF7,0x11,0x02,0x0B] + flagsByte + start_hour + start_minute + end_hour + end_minute +
 * brightness + 3 color bytes. flagsByte = slot_id (bits 0-1) | mode_bit | rgb_flag(0x40):
 * DISABLED=0x80, OCCUPANCY=0x00 (implicit else), VACANCY=0x20, SIMPLE=0x10 (from the app's own
 * if/else-if chain here, not MotionSensorResponseMode's raw ordinals - see the iOrdinal
 * branches below). Color tail: CCT -> [pct,0x00,0x00]; RGB -> [r,g,b]; RevealColor throws
 * UnsupportedOperationException (not encodable).
 * cync-lan: op_code=0x8E, cmd_code=0x14 (predicted via the length formula: 7+13=20=0x14),
 * repeat_op_code=False, targets an individual device (not a group MeshAddress - the real app
 * fans this out per-device rather than sending once to a group address).
 * Confirmed against cync-lan's own real-hardware testing for the indicator-LED sibling command
 * (SetStatusIndicatorSettingsCommand, identical DefaultImpls.c -> h() dispatch path) - see
 * cync-lan repo's docs/mesh_opcodes.md. This specific command has NOT itself been tested against
 * real hardware. Also requires the sensor be physically woken (hold off button ~5s until LED
 * turns green) before it will accept the write - see mesh_opcodes.md's "Operational
 * prerequisite" section.
 * Confidence: payload layout/op_code=0x8E confirmed via decompiled source + indirect real-
 * hardware evidence (sibling command); cmd_code=0x14 is predicted, not independently confirmed;
 * this command's own real-hardware behavior is unverified
 */
```

```java
     * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo]
     * f34829r (OPCODE_BYTES) = {0xF7,0x11,0x02,0x0B}. NOT the real outer op_code - it's the
     * leading bytes of the payload sent under the real outer op_code 0x8E, via
     * CommandDelegate.h(). See class-level note above.
     * Confidence: confirmed via decompiled source; op_code=0x8E confirmed by analogy to the
     * indicator-LED sibling command's real-hardware test
     */
```


## `com/gelighting/cbygekit/services/devices/command/SetMotionSensorSettingsCommand.java`

```java
 * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo]
 * Motion/ambient-light sensor settings command. This class's own opcode array (f34834q below,
 * {0xF7,0x11,0x02,0x07}) is NOT the real outer op_code - the Xlink send path (mo14013g, the
 * doc's "g()" naming collision aside - this is DeviceCommand's own g, not
 * XlinkCommandDelegate's) dispatches via XlinkCommandDelegate.DefaultImpls.c(...), which
 * forwards to CommandDelegate.h(), which hardcodes the real outer op_code to 0x8E and treats
 * this array as the first bytes of the payload instead. Full payload:
 * [0xF7,0x11,0x02,0x07,type_discriminator(1=motion,2=ambient),enabled,sensitivity,delay_s,
 * deactivation_s,...] (12 bytes total, cync-lan sends the leading 0xF7 as payload data).
 * cync-lan: op_code=0x8E, cmd_code=0x13 (predicted via the length formula, not confirmed
 * against a live capture), repeat_op_code=False.
 * Confirmed against cync-lan's own real-hardware testing for the indicator-LED sibling command
 * (SetStatusIndicatorSettingsCommand, identical DefaultImpls.c -> h() dispatch path, tested and
 * confirmed working after the same 0x8E fix) - see cync-lan repo's docs/mesh_opcodes.md.
 * Also note: real Cync app requires physically waking the sensor first (hold off button ~5s
 * until LED turns green = the device's ordinary mesh online status) before it accepts a
 * settings write - see mesh_opcodes.md's "Operational prerequisite" section.
 * Confidence: op_code=0x8E/dispatch path confirmed via decompiled source + indirect real-
 * hardware evidence (sibling command); cmd_code=0x13 is predicted, not independently confirmed
 */
```

```java
     * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo]
     * f34834q (OPCODE_BYTES) = {0xF7,0x11,0x02,0x07}. NOT the real outer op_code - it's the
     * leading bytes of the payload sent under the real outer op_code 0x8E, via
     * CommandDelegate.h(). See class-level note above.
     * Confidence: confirmed via decompiled source; op_code=0x8E confirmed by analogy to the
     * indicator-LED sibling command's real-hardware test
     */
```


## `com/gelighting/cbygekit/services/devices/command/SetStatusIndicatorSettingsCommand.java`

```java
 * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo]
 * "Indicator LED ring" command. This class's own opcode array (f34943x below,
 * {0xF7,0x11,0x02,0x06}) is NOT the real outer op_code - N() (mo14023N) dispatches via
 * XlinkCommandDelegate.DefaultImpls.c(...), which forwards to CommandDelegate.h(), which
 * hardcodes the real outer op_code to 0x8E and treats this entire array as the first bytes of
 * the payload instead. m14117Q() builds the full 7-byte payload as
 * [0xF7,0x11,0x02,0x06] + [(mode<<4)|color, brightness(1-100), wifi_disconnect_flag(0/1)] -
 * sent under op_code=0x8E, cmd_code=0x0E (predicted via cync-lan's length formula), with
 * repeat_op_code=False (no standalone repeated op_code byte, unlike 0xD0/0xF0/0xE2 families).
 * This is the SIBLING command that was real-hardware TESTED: originally sent with the wrong
 * assumption (op=0xF7) it was a total silent no-op; after applying the 0x8E fix it was
 * confirmed working by the user. This confirmation is the primary evidence backing the same fix
 * on SetMotionSensorSettingsCommand, SetMotionSensorScheduleCommand, and ExecuteSceneCommand
 * (all route through the identical DefaultImpls.c -> h() path).
 * Confidence: confirmed against real hardware (this specific command); op_code/dispatch path
 * also confirmed via decompiled source. cmd_code=0x0E is likewise now proven, not just predicted.
 */
```

```java
     * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo]
     * f34943x (OPCODE_BYTES) = {0xF7,0x11,0x02,0x06}. This is NOT the real outer op_code -
     * it's the leading bytes of the payload sent under the real outer op_code 0x8E, via
     * CommandDelegate.h(). Confirmed working on real hardware after the 0x8E fix.
     * Confidence: confirmed against real hardware
     */
```


## `com/gelighting/cbygekit/services/devices/command/SetWifiCommand.java`

```java
 * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
 * WiFi credential handoff - Cync/GE-specific, layered on top of the generic Telink command protocol
 * (no cleartext credentials are ever sent; uses the exact same MIC/keystream encrypted mesh-command
 * path as every other command, via TelinkDeviceBleManager.m14326L/"writeCommand"). The one piece of
 * this whole protocol with no prior-art cross-validation found (python-dimond/python-laurel never
 * implement WiFi handoff).
 * f34989z = {0xF6, 0x11, 0x02, 0x02} - fixed opcode/vendor-ID/sub-discriminator prefix prepended to
 * every chunk (see mo14060M below). Inner plaintext, built by Utilities.m13402k BEFORE chunking:
 *   [totalChunks(1B)][len(ssid)(1B)][ssid UTF-8][len(pass)(1B)][pass UTF-8][0x01][deviceType.id(1B)]
 * split into 8-byte chunks by Utilities$chunkByteArray$1, each chunk prefixed with a 1-based running
 * index byte. Each `f34989z + chunk` blob (13 bytes: 1 sub-opcode 0xF6 is packet[7], 0x11/0x02 vendor
 * ID at packet[8:10], 0x02 WiFi sub-discriminator at packet[10], chunk-index-again at packet[11],
 * up to 8 payload bytes at packet[12:20]) becomes commandBody for one writeCommand call - and the
 * chunk's own leading index byte (`b = bArr[0]` below) is passed through as the `i` parameter to
 * writeCommand, which places it at packet[2] (normally the unused byte for ordinary commands) - NOT
 * the opcode position. Target MeshAddress is always `null` here, which
 * TelinkBleDeviceController.m14192x resolves to MeshAddress.f31063g = MeshAddress(0), the
 * SELF_ADDRESS sentinel for an unprovisioned device with no assigned mesh address yet.
 * Confidence: High confidence, decompile-only but fully traced end-to-end against the confirmed packet-encryption model.
 */
```


## `com/gelighting/cbygekit/services/devices/command/StatusNotificationQueryCommand.java`

```java
 * [cync-lan reverse-engineering note - see docs/cync_automations.md in the cync-lan repo]
 * Traced for CreateSceneHubCommand/CreateScheduleHubCommand's response-side, prompted by
 * cync-lan wanting to build a real create_scene()/create_schedule() that needs the Hub-allocated
 * scene/schedule ID back to chain AddDeviceSceneCommand/AddAutomationHubCommand. Full path:
 * StatusNotificationQueryCommand.mo14013g/mo14012f (executeXlinkCommand/executeTelinkCommand)
 * -> m14129I/m14128H (initNotificationObserver) -> mo14023N/mo14060M (send the actual request).
 * Notice the ORDER: the notification observer Flow is subscribed BEFORE the outgoing request is
 * sent (mutex f35028o gates it) - this is a listener/push architecture, not polling, and it's set
 * up first specifically to avoid a race where the response could arrive before anything is
 * listening for it.
 *
 * CORRELATION MECHANISM (how an incoming frame is matched to THIS specific outstanding query):
 * Two independent filters are chained on the shared incoming-notification Flow:
 *   1. TYPE match (StatusNotificationQueryCommand$initNotificationObserver$$inlined$mapNotNull$2):
 *      KClasses.safeCast(this.f35027n /* the T KClass passed to the constructor, e.g.
 *      HubCreateSceneNotification::class *\/, notification) - only notifications of the exact
 *      expected Kotlin class pass. But by construction this is ALREADY guaranteed upstream: see
 *      XlinkNotificationParser.m14437a() below - only an incoming frame whose op_code byte maps to
 *      HUB_CREATE_SCENE ever gets turned into a HubCreateSceneNotification instance at all. So the
 *      "type" filter here is really a redundant safety net on top of the op_code-keyed dispatch.
 *   2. MSGID match (StatusNotificationQueryCommand$initNotificationObserver$$inlined$filter$2,
 *      C20052.emit): `statusNotificationQueryCommand.getF34677y() || statusNotification.getF36405c()
 *      == null || Intrinsics.areEqual(statusNotification.getF36405c(), Boxing.boxInt(msgId))` where
 *      `msgId` is the SAME 4-byte msgId (xlinkCommandDelegate.getF37789c()) that was embedded in the
 *      OUTGOING request frame's msgId field, and `statusNotification.getF36405c()` is the msgId the
 *      Hub echoed back in the RESPONSE frame - set by XlinkNotificationParser.m14437a() at the very
 *      end (`abstractStatusNotification.f36405c = Boxing.boxInt(frame.f38037a)`, where f38037a is the
 *      frame's own decoded msgId field). So: op_code (implicit, via which notification class gets
 *      constructed at all) + msgId echo-back is the FULL correlation key - not a queue/sequence
 *      number, not "next packet of this type wins" (that's only the fallback when a command doesn't
 *      care, via getF34677y()/"accept any" or when a notification legitimately carries no msgId,
 *      e.g. spontaneous ANNOUNCE-direction frames). CreateSceneHubCommand/CreateScheduleHubCommand
 *      don't override getF34677y(), so for them it's a strict msgId-equality check against their own
 *      request's msgId. This means a real client MUST echo the exact msgId from the request frame's
 *      bytes[1:5] back in the response frame's bytes[1:5] to correlate - confirmed structurally here,
 *      not just assumed.
 *
 * TIMEOUT: two independent layers, both disabled unless the concrete command overrides a getter.
 *   1. Overall query deadline: getF35006A(), default 10_000ms, used by awaitResult()/
 *      awaitMulticastResult() (mo14029c/mo14028b) to race a generic timeout util
 *      (ExtensionsKt.m15016i) against collecting the first item off the completion SharedFlow
 *      (f35030q). On expiry this resolves to a Result.Err(CompletionException) (f35026u,
 *      "Completion") - NOT a thrown exception propagating to the caller; the caller gets back a
 *      wrapped Kotlin Result (github.com/michaelbull/result) they must inspect, same as a
 *      successfully-parsed notification would arrive as Result.Ok(T).
 *   2. Optional secondary "notification-stream inactivity" timeout: getF34557z()/getF34666A(),
 *      default 0 (= disabled; only wired up if > 0). If enabled, StatusNotificationQueryCommand$
 *      initNotificationObserver$16$1/$11 races a delay against the flow's completion StateFlow and,
 *      on expiry, THROWS DeviceCommandResultTimeoutException, which is caught and re-emitted as an
 *      Err into the same completion SharedFlow (f35030q) rather than propagating raw.
 *   Neither CreateSceneHubCommand nor CreateScheduleHubCommand override getF34557z()/getF34666A(),
 *   so only the 10-second overall deadline applies to them in practice.
 *
 * DISPATCHER: push-based Flow subscription (xlinkCommandDelegate.mo14051c(...)), not polling. The
 * underlying Flow is fed by the SAME shared incoming-frame decode pipeline used for every other
 * notification type app-wide - see XlinkNotificationParser.m14437a(), which is keyed purely by
 * op_code (EnumMap<XlinkCommandCode, StatusNotificationParser>: HUB_CREATE_SCENE ->
 * HubCreateSceneNotification.XlinkParser, HUB_CREATE_SCHEDULE -> HubCreateScheduleNotification.
 * XlinkParser) - there is no separate "response channel"; it's the exact same incoming-frame stream
 * every ordinary status/ack notification rides.
 *
```


## `com/gelighting/cbygekit/services/devices/command/ToggleAutomationCommand.java`

```java
 * [cync-lan reverse-engineering note - see docs/cync_automations.md in the cync-lan repo]
 * NOT the same command as ToggleAutomationHubCommand (also in this package) despite the similar
 * purpose - genuinely different class, different dispatch path, different payload:
 * - OPCODE_BYTES (f35217r below) = {-27, 17, 2} = {0xE5, 0x11, 0x02}, a *different* 3-byte
 *   payload-prefix than AddDeviceSceneCommand's {0xEE, 0x11, 0x02} noted elsewhere in this repo's
 *   docs, but the same shape (a 3-byte array intended to be prepended to an outer envelope).
 * - mo14013g (XLink path) dispatches via XlinkCommandDelegate.DefaultImpls.m14394c(...) - i.e.
 *   the SAME kind of array-as-payload-prefix call used by the confirmed "0x8E-relay bug" family
 *   (see this repo's mesh_opcodes.md "CORRECTION" section on SetStatusIndicatorSettingsCommand
 *   and AddDeviceSceneCommand's non-hub-routed path): the leading {0xE5,0x11,0x02,...} bytes are
 *   very likely misread as an op_code by cync-lan-style envelope builders, with the real outer
 *   op_code actually hardcoded elsewhere (0x8E for the confirmed siblings) - NOT independently
 *   confirmed for this specific class, but the dispatch shape matches that bug pattern closely
 *   enough to flag.
 * - Also implements mo14012f (Telink/BLE path) with the identical payload bytes.
 * - Payload: {0xE5,0x11,0x02} + actionByte(3=enable/4=disable) + scheduleId truncated to
 *   ONE byte ((byte) this.f35218n.f41870b) - contradicts ToggleAutomationHubCommand's 2-byte-LE
 *   scheduleId encoding for the same logical field.
 * - Unlike ToggleAutomationHubCommand, this class does NOT touch SceneId, XlinkTranslatorKt, or
 *   Frame at all - no raw HDLC-style frame here, so it doesn't carry the same
 *   TCP-relay-vs-BLE-GATT transport ambiguity as the Hub-command family.
 * Confidence: payload bytes and dispatch call confirmed via decompiled source; the "0x8E-bug"
 * classification is plausible by strong structural analogy, not independently confirmed for this
 * exact class (no real outer op_code traced for the 0xE5 prefix specifically).
 */
```


## `com/gelighting/cbygekit/services/devices/command/ToggleAutomationHubCommand.java`

```java
 * [cync-lan reverse-engineering note - see docs/cync_automations.md in the cync-lan repo]
 * Real outer op_code: HUB_TOGGLE_AUTOMATION = 0x93, confirmed here as (byte) -109 (mo14013g
 * below). This is the "hub-scoped" enable/disable toggle for a Schedule - see also
 * ToggleAutomationCommand (separate class in this same package) for a structurally different,
 * per-device toggle that goes through the normal envelope path instead.
 * Payload: WriteBuffer(52) = scheduleId as 2-byte LE (offset 0-1) + sceneId as 4-byte LE
 * (offset 2-5) - the SAME logical field (a scene/schedule ID) written 2 different widths across
 * this command vs. DeleteSceneHubCommand/DeleteScheduleHubCommand's 2-byte scheme, a real
 * app-code quirk, not an assumption - then the write cursor jumps to offset 32, leaving a
 * 26-byte zero gap (offset 6-31), a zero uint16 (offset 32-33), the enabled flag as 1 byte
 * (offset 34, 0/1), one zero byte (offset 35), and 16 zero-padding bytes (offset 36-51) = 52
 * bytes total.
 * Builds its own complete wire frame via XlinkTranslatorKt.m14449a() (not Frame.m14440a()) and
 * dispatches through the raw pre-framed xlinkCommandDelegate.mo14053e(), bypassing the normal
 * envelope builder.
 * UNCONFIRMED, genuinely open question: whether this PPP/HDLC-style 0x7E-delimited,
 * byte-stuffed frame (Xlink.m14391a()) rides over the same TCP relay cync-lan intercepts, or is
 * BLE-GATT-specific. cync-lan wired this payload through its own PacketBuilder/TCP envelope as a
 * working hypothesis (cync_lan.experimental_toggle_automation), pending real-hardware
 * confirmation.
 * Confidence: op_code/payload/dispatch-path confirmed via decompiled source; wire-transport
 * question explicitly unresolved.
 */
```


## `com/gelighting/cbygekit/services/devices/controller/TelinkBleDeviceController.java`

```java
 * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
 * Per-device controller layer sitting above TelinkDeviceBleManager - resolves target addresses and
 * dispatches DeviceCommands. Key methods for BLE provisioning:
 *  - mo14149i (~line 542, "sendCommand"): the entry point every DeviceCommand goes through,
 *    including SetWifiCommand (see DeviceWifiConnectionManager.m13937d, which calls
 *    `deviceController.mo14149i(setWifiCommand, null, ...)`).
 *  - m14192x (~line 1001, "getRealAddress"): resolves a null/self target MeshAddress. For a
 *    freshly-discovered, not-yet-provisioned device (DeviceId.f35953b/"index"==0), this returns
 *    MeshAddress.Companion.m13644c(0) = MeshAddress.f31063g, the SELF_ADDRESS sentinel
 *    (MeshAddress(0) - confirmed at MeshAddress.java:110). SetWifiCommand always passes `null` as its
 *    target, so this is the exact resolution path that makes WiFi-handoff commands address
 *    themselves to SELF when provisioning a brand-new device.
 *  - mo14159k (~line 779, "pairMesh"): controller-level wrapper that calls down into
 *    TelinkDeviceBleManager$pairMesh$2 (the actual mesh-credential-handoff step); its coroutine
 *    continuation is TelinkBleDeviceController$pairMesh$1.java.
 * Confidence: Highest confidence for the SELF_ADDRESS resolution (cross-checked against MeshAddress.java); High confidence, decompile-only for the dispatch/pairMesh plumbing.
 */
```

```java
     * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
     * "getRealAddress". A null target resolves to MeshAddress.Companion.m13644c(this.f35362b.f35953b)
     * - for a fresh device (DeviceId index 0) that's MeshAddress.Companion.m13644c(0), which returns
     * MeshAddress.f31063g = MeshAddress(0), the confirmed SELF_ADDRESS sentinel used to address
     * WiFi-handoff/pairing commands to a device that has no assigned mesh address yet.
     * Confidence: Highest confidence - cross-checked directly against MeshAddress.java's static initializer (line ~110: f31063g = new MeshAddress(0)).
     */
```


## `com/gelighting/cbygekit/services/devices/model/AvailabilityState.java`

```java
 * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo,
 * "Operational prerequisite: motion sensors must be woken before settings/schedule writes"]
 * This sealed class's 5 variants (Establishing, NoLink, None, Offline, Online) are the ONLY
 * device-availability states in the app - there is no special "discoverable"/BLE-scan variant.
 * This confirms the wake-up flow's "make it discoverable" gate is really just waiting for this
 * StateFlow (exposed via DeviceManager.mo13807i(), see DeviceManager.java) to flip to Online, the
 * same signal used for every device type's ordinary online/offline status, equivalent to
 * cync-lan's own `bridge.is_online(dev_id)`.
 * Confidence: confirmed via decompiled source.
 */
```


## `com/gelighting/cbygekit/services/devices/model/LightRunMode.java`

```java
     * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo,
     * "Full light-run-mode incl. MultiColor/MusicShow" section]
     * These 5 constants are the modeCode values sent as the first payload byte after
     * [0x11, 0x02, 0x07] by SetLightRunModeCommand (op 0xE2, cmd_code reuses set_lightshow's
     * confirmed 0x0E) - full table:
     *   0 = Static        (MODE_STATIC)      - always index 0
     *   1 = LightShow      (MODE_LIGHT_SHOW)  - index 1-9, 65-67 factory, 10-32 custom
     *   2 = MusicShow      (MODE_MUSIC_SHOW)  - index 1-8, 65 factory, 10-32 custom
     *                        (NOT raw audio data - device does audio-reactivity locally via its
     *                        own mic; the wire command just selects a preset index)
     *   3 = Reveal         (MODE_REVEAL)      - always index 0
     *   4 = MultiColor     (MODE_MULTI_COLOR) - index 1-2 factory, 3-32 custom (activates a saved
     *                        scheme only; uploading custom scheme data is a separate opcode,
     *                        SetMultiColorSegmentsCommand)
     * cync-lan's `devices.py` `set_light_effect()` + `const.py`'s `LIGHT_RUN_MODE_EFFECTS` cover
     * all 5 modes already, exposed via the light entity's `effect`/`effect_list` attribute.
     * Confidence: confirmed via decompiled source.
     */
```


## `com/gelighting/cbygekit/services/devices/model/notification/HubCreateSceneNotification.java`

```java
 * [cync-lan reverse-engineering note - see docs/cync_automations.md in the cync-lan repo]
 * Response to CreateSceneHubCommand (op_code HUB_CREATE_SCENE = 0x10). Byte-exact parsing
 * (XlinkParser.mo14294a below): input is the notification's PAYLOAD only (post outer HDLC framing
 * - msgId/flag/op_code/len/checksum already stripped by XlinkNotificationParser.m14437a() before
 * this parser ever runs) as a little-endian ByteBuffer:
 *   offset 0 (1 byte):  errorCode - read as a signed byte, stored as-is (f36461f)
 *   offset 1-2 (2 bytes, u16 LE): allocated sceneId - read via ByteBuffer.getShort() (signed 16-bit),
 *     then sign-corrected to unsigned (`if (i < 0) i += 65536`) - so the real range is 0-65535,
 *     stored as an Int (f36460e).
 * That's it - only 3 bytes are consumed; nothing else in the payload is read by this parser. The
 * constructor call is `new HubCreateSceneNotification(i, b)` i.e. (sceneId, errorCode) - confirmed by
 * the toString()/equals() ordering (f36460e=sceneId printed first, f36461f=errorCode second).
 * Correlation to the specific outgoing CreateSceneHubCommand: see the note on
 * StatusNotificationQueryCommand.java - op_code (HUB_CREATE_SCENE, which is what routes a raw frame
 * to THIS class in the first place, per XlinkNotificationParser's EnumMap) plus an exact msgId
 * echo-back match against the request's msgId. No sequence/queue mechanism beyond that.
 */
```


## `com/gelighting/cbygekit/services/devices/model/notification/HubCreateScheduleNotification.java`

```java
 * [cync-lan reverse-engineering note - see docs/cync_automations.md in the cync-lan repo]
 * Response to CreateScheduleHubCommand (op_code HUB_CREATE_SCHEDULE = 0x92 / (byte) -110). Parsing
 * logic (XlinkParser.mo14294a below) is byte-for-byte IDENTICAL to HubCreateSceneNotification's:
 * little-endian ByteBuffer over the notification payload only (post outer HDLC frame stripped) -
 *   offset 0 (1 byte): errorCode, signed byte, stored as-is (f36465f)
 *   offset 1-2 (2 bytes, u16 LE): allocated scheduleId, ByteBuffer.getShort() then sign-corrected
 *     to unsigned (`if (i < 0) i += 65536`), range 0-65535, stored as Int (f36464e)
 * Constructor call `new HubCreateScheduleNotification(i, b)` = (scheduleId, errorCode) - confirmed
 * by toString()/equals() field order. Only 3 bytes read; nothing else in the payload consumed.
 * This is the scheduleId that RoutinesService.m14800Q() unwraps to then immediately build an
 * AddAutomationHubCommand (op_code 0x95) carrying scheduleId+sceneId+day-bitmask+time - see the
 * detailed note on CreateScheduleHubCommand.java for that full follow-up sequence.
 * Correlation to the specific outgoing CreateScheduleHubCommand: see the note on
 * StatusNotificationQueryCommand.java - op_code (HUB_CREATE_SCHEDULE, routes the raw frame to THIS
 * class via XlinkNotificationParser's EnumMap) plus an exact msgId echo-back match against the
 * request's msgId. No separate sequence/queue mechanism.
 */
```


## `com/gelighting/cbygekit/services/devices/telink/C2184d.java`

```java
 * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
 * This is JADX's renamed form of an anonymous lambda originally inlined in
 * TelinkDeviceBleManager.m14334v ("authenticate") - it's the ReadRequest DataReceivedCallback that
 * fires when the device responds on the pairing characteristic (Telink.f28876j) after the initial
 * pairing write. NOT directly named in the doc, but it IS the code that resolves two of the doc's
 * "genuinely open" items:
 *  1. Mutual-auth verification: mo14353a (below) DOES perform real verification, contrary to the
 *     doc's finding that python-dimond skips it. It reconstructs an expected value from the device's
 *     own R_dev (characterData[1:9]) and XOR(name,pass), via
 *     `R_dev[0:8] + Telink.m13404b(pad16(R_dev), XOR(name,pass))[0:8]`, and compares it against the
 *     full response `characterData[1:17]` (~line 116). Only on a match does it proceed to derive and
 *     store the session key; on mismatch the session key MutableStateFlow is set to null (pairing
 *     effectively fails). So the real, shipped Cync app DOES check this - python-dimond's
 *     "don't verify" shortcut is real-world-safe only because a from-scratch client controls both
 *     sides and doesn't need the defensive check the official app performs.
 *  2. Session key derivation (~lines 117-121): sessionKey = Telink.m13404b(XOR(name,pass), R_app[0:8]
 *     + R_dev[0:8]) where R_app = Telink.f28877k[0:8] (the SAME fixed constant used in the write path
 *     in m14334v, NOT SecureRandom output) and R_dev = characterData[1:9]. This exactly matches
 *     python-dimond's generate_sk, and confirms f28877k is a genuine fixed constant, not a
 *     placeholder later overwritten.
 * Confidence: High confidence, decompile-only but internally consistent and traced end-to-end against the confirmed handshake algorithm.
 */
```


## `com/gelighting/cbygekit/services/devices/telink/TelinkDeviceBleManager$pairMesh$2.java`

```java
 * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
 * The mesh-credential-handoff step ("pairMesh") - runs AFTER the generic Telink session-key handshake
 * (TelinkDeviceBleManager.m14318B/m14334v + C2184d) has already established a session key. This is
 * the Cync/GE-specific step that gives a device its PERMANENT mesh name/password/LTK. For each of
 * mesh name, mesh password, and LTK (Telink.f28879m default, or meshCredentials.f36188c if set):
 *   1. UTF-8 encode, pad to 16 bytes (Telink.m13406d)
 *   2. AES-ECB-encrypt with the just-derived session key (Telink.m13404b) -> 16 bytes, take [0:8]
 *   3. prepend a literal opcode byte: 4=NAME, 5=PASSWORD, 6=LTK (see Telink.OPCODE's note - these
 *      line up with enum ordinals 3/4/5 + 1, a plausible but not fully proven ordinal->literal
 *      mapping)
 *   4. pad the resulting 9 bytes to 17 and write to the pairing characteristic (Telink.f28876j)
 * All three writes are queued together, then a ReadRequest confirms pairing via booleanRef; on
 * failure throws DevicePairingException("Pairing was not confirmed by device"). This is the step
 * python-dimond/python-laurel provide NO cross-validation for (they only join already-provisioned
 * meshes), so it remains decompile-only.
 * Confidence: High confidence, decompile-only but fully traced end-to-end and internally consistent with the confirmed pairing/encryption primitives.
 */
```


## `com/gelighting/cbygekit/services/devices/telink/TelinkDeviceBleManager.java`

```java
 * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
 * The core Cync/GE Telink-Mesh-over-BLE connection manager. Extends Nordic Semiconductor's
 * `no.nordicsemi.android.ble.BleManager` (generic Android-BLE-Library GATT-connection helper) - the
 * Nordic base class itself is not annotated here, only the Cync-specific overrides/usages are.
 * Key methods for BLE provisioning, all confirmed against docs/ble_provisioning_protocol.md:
 *  - mo14329e (~line 1596) / mo14330f (~line 1610): Nordic BleManager service-discovery callbacks -
 *    mo14330f binds Telink.f28872f..f28876j (service/pairing/status/command/OTA characteristics)
 *    plus the standard BLE DIS pair; mo14329e gates on the byte-reversed f28869c/f28871e pair.
 *  - m14334v ("authenticate", ~line 1773): the initial connect-time pairing characteristic write -
 *    see its own note below for a real finding about the fixed (non-random) "R_app" bytes used here.
 *  - m14318B ("getSessionKey", ~line 911): awaits/derives the AES session key via the pairing
 *    characteristic round-trip (matches python-dimond's connect()/generate_sk exactly).
 *  - m14326L ("writeCommand", ~line 1229): builds+encrypts the 20-byte command packet (seq/chunk-idx/
 *    MIC/target/opcode/vendorID/payload) and performs the MIC+keystream AES-ECB encryption - see its
 *    own note below.
 * Confidence: Highest confidence - validated against real, working, cloned code (python-dimond) for the crypto/packet-layout pieces; High confidence, decompile-only for pairMesh/discovery-gate specifics.
 */
```

```java
     * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
     * "getSessionKey" - awaits/derives the AES session key, delegating to
     * TelinkDeviceBleManager$getSessionKey$2 for the actual pairing-characteristic round trip. Called
     * both by m14326L/writeCommand (~line 1247, every ordinary command write) and by
     * TelinkDeviceBleManager$pairMesh$2 before the mesh-credential handoff - i.e. this transparently
     * forces a full connect+pair+session-key sequence on demand for the very first command sent, per
     * the doc's "step-1-vs-step-3 ordering" resolution. Real session-key math itself lives in
     * TelinkDeviceBleManager$..d (C2184d.mo14353a, the pairing-characteristic read callback) - see
     * that file's note for a real finding about mutual-auth verification.
     * Confidence: Highest confidence - validated against real, working, cloned code (python-dimond).
     */
```

```java
     * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
     * "writeCommand" - builds and encrypts the 20-byte command packet, confirmed against
     * python-dimond's real send_packet/encrypt_packet:
     *   packet[0:2]  seq (random)
     *   packet[2]    the `i` parameter here - 0x00 for ordinary commands, but SetWifiCommand passes
     *                its chunk index here instead (see SetWifiCommand.java's note)
     *   packet[3:4]  MIC, written after the AES-ECB fold-in-and-hash below
     *   packet[5:6]  target MeshAddress, little-endian (destination.f31064a byte-swapped)
     *   packet[7:]   commandBody (opcode + vendor ID + payload)
     * The MIC/keystream loop above this (auth_nonce/authenticator -> MIC, iv -> keystream, both
     * single AES-ECB blocks via Telink.m13404b, XOR-folded against packet[5:20]) matches the doc's
     * "Command encryption" section exactly, including the 20ms-group/320ms-otherwise inter-command
     * throttle in TelinkDeviceBleManager$writeCommand$2.java:222 (confirmed present, same line number
     * as the doc cites).
     * Confidence: Highest confidence - validated against real, working, cloned code (python-dimond).
     */
```

```java
     * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
     * NEW FINDING beyond the doc: Nordic BleManager's "isRequiredServiceSupported" gate. Looks up
     * gatt.getService(Telink.f28869c) then .getCharacteristic(Telink.f28871e) - the mysterious
     * byte-reversed service/characteristic pair the doc flagged as "purpose not determined" - and
     * returns false (rejecting the whole connection before mo14330f below even runs) if either is
     * missing. So this pair's real functional role is a device-capability sentinel check, not part
     * of the pairing or command protocol itself.
     * Confidence: High confidence, decompile-only but now traced to a concrete call site.
     */
```

```java
     * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
     * Nordic BleManager's GATT characteristic-binding callback (runs after mo14329e above passes).
     * Binds every Telink.* UUID field to its role: f28872f=primary service, f28876j=pairing char
     * (this.f36845s), f28873g=status char (this.f36846t, notifications enabled here), f28874h=command
     * char (this.f36847u), f28875i=OTA char (this.f36850x), f28868b/f28870d=standard BLE Device
     * Information Service / Firmware Revision String (this.f36848v). Note: the v2 decompile's
     * `Intrinsics.checkNotNullExpressionValue(uuid, "<get-XXX_UUID>(...)")` calls below recover the
     * original Kotlin property names (TELINKSERVICE_UUID, TELINKPAIRCHAR_UUID, etc.) - independent
     * confirmation of every UUID role already documented on the Telink class itself.
     * Confidence: Highest confidence - validated against real, working, cloned code (python-dimond) for the four Telink UUIDs.
     */
```

```java
     * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
     * "authenticate" - the real initial pairing-characteristic write (this + the C2184d read-response
     * callback registered on the same RequestQueue together implement the full handshake python-dimond
     * calls connect()). Two paths:
     *  - name=="telink_mesh1" && password==default("123"): writes the fully pre-baked
     *    Telink.f28878l (17 bytes) verbatim - the factory-default/unprovisioned-device case.
     *  - otherwise: writes [0x0C] + Telink.f28877k[0:8] + key_encrypt(name,pass,key=f28877k)[0:8].
     * NEW FINDING beyond the doc: in BOTH paths, the "R_app" 8 bytes are the fixed constant
     * Telink.f28877k[0:8] = {0xA0..0xA7} - never SecureRandom output. This differs from python-dimond
     * (which generates 8 fresh random bytes per session) but is protocol-compatible; see Telink.java's
     * note on f28877k for the second confirming call site (C2184d.mo14353a's session-key derivation).
     * The literal opcode byte 0x0C here is read via an unrelated MqttWireMessage.MESSAGE_TYPE_PINGREQ
     * constant purely because both equal 12 - a decompiler/obfuscator artifact, not a real MQTT
     * reference.
     * Confidence: High confidence, decompile-only but cross-checked across two independent call sites in this v2 tree (m14334v here and C2184d.mo14353a).
     */
```


## `com/gelighting/cbygekit/services/devices/xlink/Xlink.java`

```java
     * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo]
     * This is the doc's "Xlink.a(op_code, data, msgId)" frame-builder, reached via
     * XlinkDeviceManager.CommandDelegate.g() (mo14055g). Builds:
     * [msgId(4B LE)][0xF8][op_code(1B)][length(2B LE)][data][checksum], then 0x7E-delimits and
     * byte-stuffs it (0x7D/0x7E escaping, see the loop below) - an HDLC/PPP-style framing,
     * confirmed distinct from cync-lan's own captured 5-byte-header wire format in
     * packet_structure.md (no delimiters/escaping there). The 0xF8 marker
     * (pdqbbbp.dpdqppp = 248) is a real constant shared with cync-lan's own inner-packet header's
     * leading byte. This whole io.xlink.wifi.sdk/xlink.legacy pathway carries a @Deprecated tag
     * on its writer thread (TcpPacketWriter.java) - likely the phone app's OLDER command channel,
     * not necessarily byte-identical to the device-facing protocol cync-lan replicates. The
     * length field written here (see WriteBuffer.d/m14444d) is what resolves cync-lan's own
     * `cmd_code` mystery - see the note on that method.
     * Confidence: plausible, not independently confirmed (whether this HDLC framing is what
     * actually rides the TCP wire to real devices, vs. being purely a legacy/alternate path)
     */
```


## `com/gelighting/cbygekit/services/devices/xlink/XlinkCommandCode.java`

```java
 * [cync-lan reverse-engineering note - see docs/cync_automations.md in the cync-lan repo]
 * Full opcode table for the Scenes/Schedules "Hub" commands, per this repo's mesh_opcodes.md
 * ("Scenes control"/"Groups control" sections) and cync_automations.md:
 *   HUB_CREATE_SCENE      = 0x10 (below: Tnaf.POW_2_WIDTH == 16 decimal == 0x10)
 *   HUB_DELETE_SCENE      = 0x1F (below: (byte) 31)
 *   HUB_CREATE_SCHEDULE   = 0x92 (below: (byte) -110)
 *   HUB_TOGGLE_AUTOMATION = 0x93 (byte -109) - in THIS jadx run, appears mislabeled as a
 *     duplicate "HUB_WIFI_CONFIGURE((byte) -109)" entry below: jadx failed to fully restore this
 *     pseudo-enum (see "Enum visitor error" above) and reused generic constant names for values
 *     it couldn't otherwise name - go by the byte value, not the label, for this entry.
 *   HUB_DELETE_SCHEDULE   = 0x94 (byte -108) - similarly mislabeled "HUB_PASSTHROUGH_8C((byte)
 *     -108)" below in this run.
 * Confirmed against the command classes themselves: DeleteSceneHubCommand uses (byte) 31,
 * DeleteScheduleHubCommand uses (byte) -108, ToggleAutomationHubCommand uses (byte) -109,
 * CreateScheduleHubCommand uses (byte) -110 - all matching the table above.
 * Confidence: confirmed via decompiled source (byte values); the specific enum-entry labels in
 * this jadx run are an artifact of a failed enum-recovery pass, not real distinct constants.
 */
```


## `com/gelighting/cbygekit/services/devices/xlink/XlinkCommandDelegate.java`

```java
 * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo]
 * The interface behind XlinkDeviceManager.CommandDelegate (the concrete implementation, which
 * carries the doc's f()/g()/h() dispatch methods - see the class-level note there for the full
 * picture). Here they're mo14054f (f, trustworthy envelope builder with a real op_code param),
 * mo14055g (g, builds the older @Deprecated HDLC-style frame via Xlink.a()), and mo14056h
 * (h, hardcodes the outer op to 0x8E - source of the "0x8E mesh-relay bug" command family).
 * Confidence: confirmed via decompiled source
 */
```

```java
         * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo]
         * This is the doc's "DefaultImpls.c(...)" - the call site every 0x8E-bug command goes
         * through. Callers (SetStatusIndicatorSettingsCommand.N(), SetMotionSensorSettingsCommand,
         * SetMotionSensorScheduleCommand, ExecuteSceneCommand) pass their entire opcode array as
         * one opaque byte[] with NO separate op_code argument - there simply isn't a parameter
         * for one here, only bArr/meshAddress/i/continuation/i2. This directly forwards to
         * mo14056h (h()), which is what hardcodes the real outer op_code to 0x8E. Commands that
         * instead call mo14054f (f()) directly with their own op byte (e.g. SetComboCommand,
         * ControlDeviceGroupCommand) are unaffected by this bug.
         * Confidence: confirmed via decompiled source
         */
```


## `com/gelighting/cbygekit/services/devices/xlink/XlinkDeviceManager.java`

```java
     * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo]
     * This inner class is the real dispatch layer every mesh command class ultimately calls
     * (via XlinkCommandDelegate). Three methods matter, named f()/g()/h() in the doc
     * (JADX renamed them mo14054f/mo14055g/mo14056h here):
     *  - f() (mo14054f) is the TRUSTWORTHY envelope builder: prepends a 7-byte routing prefix
     *    (3-byte msgId LE + 2 zero bytes + a real 2-byte little-endian MeshAddress destination
     *    field) to commandBody, then calls g().
     *  - h() (mo14056h) HARDCODES the outer op_code to (byte)-114 = 0x8E and calls f() with
     *    that override - this is the root cause of the whole "0x8E mesh-relay bug" family
     *    documented on SetStatusIndicatorSettingsCommand, SetMotionSensorSettingsCommand,
     *    SetMotionSensorScheduleCommand, and ExecuteSceneCommand: those classes pass their own
     *    opcode array as one opaque commandBody with no separate op argument, so it ends up
     *    misread as payload data instead of the real outer op.
     *  - g() (mo14055g) hands off to Xlink.a() (Xlink.m14391a), which builds an older,
     *    @Deprecated-tagged HDLC/PPP-style frame (0x7E-delimited, byte-stuffed) - confirmed
     *    distinct from cync-lan's own captured 5-byte-header wire format (no delimiters/escaping
     *    there). Flagged plausible-not-confirmed whether this is byte-identical to the
     *    device-facing protocol cync-lan replicates; see the doc's "TCP relay envelope research"
     *    section for the full nuance.
     * Confidence: confirmed via decompiled source (f()/h() dispatch logic); plausible, not
     * independently confirmed (whether g()/Xlink.a()'s HDLC framing matches cync-lan's TCP wire format)
     */
```

```java
         * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo]
         * This is the doc's "f()" - the trustworthy envelope builder. `b` here really is the
         * outer op_code (confirmed: -41=0xD7 for ControlDeviceGroupCommand's group-membership op,
         * -16=0xF0 for SetComboCommand). Writes msgId (3B LE) + 2 zero bytes + destination
         * (a genuine 2-byte little-endian MeshAddress, via ExtensionsKt.m13359f) + commandBody,
         * then hands off to g() (mo14055g). Commands that call this directly with their own real
         * op byte are NOT affected by the 0x8E bug below.
         * Confidence: confirmed via decompiled source
         */
```

```java
         * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo]
         * This is the doc's "h()" - the source of the "0x8E mesh-relay bug" family. Note there
         * is NO `op` parameter here at all: `bArr` is the command's *entire* opcode array (e.g.
         * SetStatusIndicatorSettingsCommand's {0xF7,0x11,0x02,0x06}) passed as one opaque blob,
         * and this method hardcodes the real outer op_code to (byte) -114 = 0x8E, then forwards
         * to f() (mo14054f) with that override. Any command whose send path reaches this method
         * (rather than calling f()/mo14054f directly with its own op byte) will have its opcode
         * array's first byte misread by anyone assuming it's the outer op_code - it's actually
         * just the first byte of the payload. Independently confirmed against a real captured
         * packet (a plug power-toggle: f8 8e 0b 00 20 00 00 00 00 ff ff f7 11 02 21 e2) decoding
         * byte-for-byte as op_code=0x8E, cmd_code=0x0B, payload=[0xF7,0x11,0x02,0x21] - see
         * PacketBuilder.build_control_packet(op_code=0x8E, repeat_op_code=False) in cync-lan's
         * src/cync_lan/packet/builder.py. Also note: unlike every other confirmed op family
         * (0xD0/0xF0/0xE2), a genuine 0x8E-family packet has NO repeated standalone op_code byte
         * between the routing section and payload - cync-lan's builder needed a
         * repeat_op_code=False flag specifically for this family.
         * Confidence: confirmed against real hardware (packet capture) and via decompiled source
         */
```


## `com/gelighting/cbygekit/services/devices/xlink/legacy/WriteBuffer.java`

```java
     * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo]
     * This is the doc's "WriteBuffer.d(length)" - resolves the cync-lan `cmd_code` mystery.
     * Called from Xlink.a() (Xlink.java's m14391a) as `length` = the byte length of `data`
     * (the 7-byte routing prefix + commandBody), written little-endian right after op_code in
     * the frame header: [msgId(4B LE)][0xF8][op_code(1B)][length(2B LE)][data][checksum].
     * This 2-byte field is NOT a semantic command code at all - it's a plain payload-length
     * field. cync-lan's own separate `cmd_code` concept (used in ITS OWN inner-packet header,
     * a different wire format from this app's) turns out to be derived from this same length via
     * a formula: cmd_code = 7 + len(op_code_byte + command_payload). Verified 3/3 against
     * cync-lan's already-confirmed production values (set_power=0x0D, set_brightness/rgb=0x10,
     * set_lightshow=0x0E), which is how cync-lan now predicts cmd_code for new commands it
     * hasn't captured live yet, instead of requiring a fresh packet capture for each one.
     * Confidence: confirmed via decompiled source (this field is a length); the cmd_code-equals-
     * length-formula itself is confirmed by matching 3/3 known values, not a live capture of the
     * formula's derivation
     */
```


## `com/gelighting/cbygekit/services/devices/xlink/legacy/XlinkTranslatorKt.java`

```java
 * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo]
 * Thin Kotlin wrapper around Xlink.a() (Xlink.m14391a) - just re-boxes its HDLC/PPP-style
 * framed byte[] output back into a legacy WriteBuffer. Part of the same
 * "older, @Deprecated-tagged command channel" pathway discussed on Xlink.java and
 * TcpPacketWriter.java - not confirmed to be byte-identical to the device-facing protocol
 * cync-lan's own TCP relay replicates.
 * Confidence: plausible, not independently confirmed
 */
```


## `com/gelighting/cbygekit/services/locations/LocationProperties$$serializer.java`

```java
 * [cync-lan reverse-engineering note - see docs/cync_automations.md in the cync-lan repo]
 * This is the source of truth for LocationProperties' raw JSON field names - see the
 * pluginGeneratedSerialDescriptor.m29539j(...) calls in the static initializer below (e.g.
 * "bulbsArray", "sceneArray", "schedules", "groupsArray"). Full JSON-key -> Kotlin-field mapping
 * documented on LocationProperties.java itself. Notably this synthetic $$serializer class DID
 * render fully in this jadx pass, unlike some other kotlinx.serialization companions in this
 * tree that jadx fails to render - if a future jadx run can't render this one, the mapping is
 * preserved in the comment on LocationProperties.java regardless.
 * Confidence: confirmed via decompiled source (literal string constants at the descriptor).
 */
```


## `com/gelighting/cbygekit/services/locations/LocationProperties.java`

```java
 * [cync-lan reverse-engineering note - see docs/cync_automations.md in the cync-lan repo]
 * Raw cloud-export DTO for a home's `properties` object (cync-lan's raw_mesh.cync / cloud_api.py
 * _parse_raw_export()). Kotlin domain field names differ from the raw JSON keys - the mapping
 * was confirmed directly against LocationProperties$$serializer.java's descriptor registration
 * (m29539j(...) calls), which DID render in this jadx pass (unlike some other synthetic
 * serializer classes elsewhere in this tree):
 *   JSON "bulbsArray"              -> Kotlin f40125g (deviceItems)
 *   JSON "standaloneDevicesArray"  -> Kotlin f40126h (standaloneDeviceItems)
 *   JSON "sceneArray"              -> Kotlin f40127i (scenesArray)
 *   JSON "schedules"               -> Kotlin f40128j (schedulesArray)
 *   JSON "groupsArray"             -> Kotlin f40129k (groupsArray, unchanged)
 * See LocationProperties$$serializer.java for the exact descriptor call sites.
 * UNVALIDATED against a real populated export for the scenes/schedules fields specifically: the
 * one real account sampled for cync-lan's research had zero scenes/schedules configured, so
 * sceneArray/schedules field names are confirmed against the app's own deserialization bytecode
 * but never cross-checked against real captured JSON containing actual entries (groupsArray/
 * bulbsArray, by contrast, WERE validated against real populated data).
 * Confidence: field-name mapping confirmed via decompiled serializer source; real-data
 * cross-check UNVALIDATED for sceneArray/schedules.
 */
```


## `com/gelighting/cbygekit/services/locations/SceneActionItem.java`

```java
 * [cync-lan reverse-engineering note - see docs/cync_automations.md in the cync-lan repo]
 * Raw JSON field names, confirmed via SceneActionItem$$serializer.java's descriptor registration:
 *   JSON "fade"       -> Kotlin f40504a
 *   JSON "bright"     -> Kotlin f40505b
 *   JSON "cctOrRgb"   -> Kotlin f40506c
 *   JSON "deviceID"   -> Kotlin f40507d (deviceId)
 *   JSON "bulbIsOn"   -> Kotlin f40508e (deviceIsOn)
 *   JSON "runMode"    -> Kotlin f40509f
 *   JSON "showIndex"  -> Kotlin f40510g
 *   JSON "schemeIndex"-> Kotlin f40511h
 * One device's captured state within a Scene's actionArray - see SceneItem.java for the parent
 * object and this repo's mesh_opcodes.md "Scenes control" section for the analogous *wire*
 * (mesh-packet) encoding of a captured device state via AddDeviceSceneCommand, which is a
 * different, independently-confirmed 6-byte binary layout (mode, brightness/param,
 * color-temp-or-254-for-RGB, R, G, B) - this JSON field set is the cloud-export shape, not the
 * mesh wire shape, and the two haven't been cross-checked field-for-field against each other.
 * UNVALIDATED against a real populated export - the one real account sampled for this research
 * had zero scenes configured.
 * Confidence: field-name mapping confirmed via decompiled source; real-data cross-check
 * UNVALIDATED.
 */
```


## `com/gelighting/cbygekit/services/locations/SceneItem.java`

```java
 * [cync-lan reverse-engineering note - see docs/cync_automations.md in the cync-lan repo]
 * Raw JSON field names, confirmed via SceneItem$$serializer.java's descriptor registration
 * (m29539j calls - two of them reuse generic string constants from unrelated classes, resolved
 * by reading those classes: StateKey.SCENE_TYPE = "sceneType" from the bundled-but-dead
 * com.thingclips.smart.scene.model.constant.StateKey - direct confirmation of this repo's
 * mesh_opcodes.md/cync_automations.md claim that the Tuya/ThingClips scene-rule engine is dead
 * code for Cync, only its generic string-constant classes get reused):
 *   JSON "sceneID"      -> Kotlin f40517a (sceneId)
 *   JSON "isReal"       -> Kotlin f40518b
 *   JSON "sceneType"    -> Kotlin f40519c (sceneTypeId)
 *   JSON "displayName"  -> Kotlin f40520d
 *   JSON "showOnHome"   -> Kotlin f40521e
 *   JSON "position"     -> Kotlin f40522f
 *   JSON "actionArray"  -> Kotlin f40523g (actions, List<SceneActionItem>)
 *   JSON "parentID"     -> Kotlin f40524h
 * UNVALIDATED against a real populated export: the one real account sampled for this research had
 * zero scenes configured, so these field names are confirmed against the app's own
 * deserialization bytecode but never cross-checked against real captured JSON.
 * Confidence: field-name mapping confirmed via decompiled source; real-data cross-check
 * UNVALIDATED.
 */
```


## `com/gelighting/cbygekit/services/locations/ScheduleItem.java`

```java
 * [cync-lan reverse-engineering note - see docs/cync_automations.md in the cync-lan repo]
 * Raw JSON field names, confirmed via ScheduleItem$$serializer.java's descriptor registration
 * (one of them, field 0, is registered via the generic string constant
 * com.thingclips.sdk.personallib.pdqppqb.pdqppqb = "id" - an unrelated Tuya "feedback" DB
 * constants class incidentally reused for its literal string value, not a real Cync-side symbol):
 *   JSON "id"          -> Kotlin f40527a
 *   JSON "displayName" -> Kotlin f40528b
 *   JSON "trigger"     -> Kotlin f40529c (ScheduleTrigger, required/non-optional field)
 *   JSON "state"       -> Kotlin f40530d (boolean)
 *   JSON "scheduleID"  -> Kotlin f40531e
 *   JSON "triggerType" -> Kotlin f40532f
 *   JSON "showOnHome"  -> Kotlin f40533g
 *   JSON "parentID"    -> Kotlin f40534h
 * OPEN QUESTION, flagged explicitly: this DTO has BOTH an "id" field (f40527a) AND a separate
 * "scheduleID" field (f40531e) with NO confirmed distinction between them anywhere in the
 * decompiled source read so far - cync-lan's cloud_api.py falls back from scheduleID to the
 * sibling id field, but which one is authoritative (or whether they're always equal) is unknown.
 * "state" (f40530d) is inferred, not confirmed, to be the schedule's enabled flag - it's simply
 * the closest boolean field on the DTO, not verified against real captured JSON or app UI wiring.
 * UNVALIDATED against a real populated export - the one real account sampled for this research
 * had zero schedules configured, so none of this has been cross-checked against real captured
 * JSON the way e.g. groupsArray was.
 * Confidence: field-name mapping confirmed via decompiled source; id-vs-scheduleID distinction
 * and "state"=enabled inference both UNCONFIRMED/UNVALIDATED.
 */
```


## `com/gelighting/cbygekit/services/locations/ScheduleTrigger.java`

```java
 * [cync-lan reverse-engineering note - see docs/cync_automations.md in the cync-lan repo]
 * Raw JSON field names, confirmed via ScheduleTrigger$$serializer.java's descriptor registration:
 *   JSON "action"    -> Kotlin f40537a (ScheduleTriggerAction, required/non-optional field)
 *   JSON "startTime" -> Kotlin f40538b
 *   JSON "endTime"   -> Kotlin f40539c (constructor hardcodes this to "" - see below)
 *   JSON "cyc"       -> Kotlin f40540d (int; required/non-optional field - almost certainly a
 *     day-of-week bitmask given ScheduleModel's Set<ScheduleDay> domain concept, NOT confirmed
 *     bit-for-bit against real data)
 * Note: the public (non-serializer) constructor above hardcodes endTime to "" regardless of what
 * callers pass, which is odd for a supposedly meaningful field - possibly vestigial, possibly
 * only meaningful on deserialize (the private/synthetic $$serializer constructor DOES accept a
 * real endTime string). Not resolved further here.
 * UNVALIDATED against a real populated export - the one real account sampled for this research
 * had zero schedules configured.
 * Confidence: field-name mapping confirmed via decompiled source; "cyc" semantics and the
 * endTime-hardcoded-to-empty behavior are both UNCONFIRMED/UNVALIDATED.
 */
```


## `com/gelighting/cbygekit/services/locations/ScheduleTriggerAction.java`

```java
 * [cync-lan reverse-engineering note - see docs/cync_automations.md in the cync-lan repo]
 * Raw JSON field names, confirmed via ScheduleTriggerAction$$serializer.java's descriptor
 * registration - this is the nested object at ScheduleItem.trigger.action, i.e. the JSON path
 * cync_automations.md refers to as "trigger.action.sceneID" (the scene a schedule fires):
 *   JSON "sceneID"        -> Kotlin f40543a - the scene this schedule trigger fires
 *   JSON "startActionID"  -> Kotlin f40544b
 *   JSON "endActionID"    -> Kotlin f40545c
 * All three are plain ints, all default to 0. startActionID/endActionID semantics not resolved
 * further here - not traced against any consumer.
 * UNVALIDATED against a real populated export - the one real account sampled for this research
 * had zero schedules configured.
 * Confidence: field-name mapping confirmed via decompiled source; startActionID/endActionID
 * semantics UNCONFIRMED; real-data cross-check UNVALIDATED.
 */
```


## `com/gelighting/cbygekit/services/motionSensor/MotionSensorServiceDefault$isOnline$1.java`

```java
 * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo,
 * "Operational prerequisite: motion sensors must be woken before settings/schedule writes"]
 * This @DebugMetadata(m="isOnline") annotation is the proof that the JADX-mangled method it
 * wraps a continuation for - MotionSensorServiceDefault.m14739t() - is the real Kotlin
 * `isOnline()` suspend fun. See the annotated m14739t() in MotionSensorServiceDefault.java: it is
 * a plain AvailabilityState.Online check, not a BLE discoverability scan.
 * Confidence: confirmed via decompiled source.
 */
```


## `com/gelighting/cbygekit/services/motionSensor/MotionSensorServiceDefault.java`

```java
 * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo,
 * "Operational prerequisite: motion sensors must be woken before settings/schedule writes"]
 * The real Cync app's "wake the device, make it discoverable" gate (hold the off button 5s
 * until the LED turns green - see DeviceSettingsWakeUpFragment) is NOT a BLE/GATT discoverability
 * scan. It is exactly the device's ordinary mesh online/offline status: m14739t() (JADX-obfuscated
 * name for isOnline(), see its @DebugMetadata below) reads DeviceManager.mo13807i()'s
 * StateFlow<AvailabilityState> and checks `instanceof AvailabilityState.Online` - the same signal
 * every device type (lights, switches, sensors) reports, equivalent to cync-lan's own
 * `bridge.is_online(dev_id)`.
 * Load-bearing gotcha: the per-device write paths (m14729B/m14730C = writeSchedule,
 * m14731D/m14732E = writeSettings) call m14739t() per target device and, if NO device in the
 * target set is online, skip building/sending the command entirely and return the pre-built
 * `Ok(Unit)` singleton (com.gelighting.cbygekit.util.UtilsKt.f43576a) - i.e. FAKE SUCCESS, not an
 * error. A caller has no way to distinguish "command sent" from "silently dropped because the
 * sensor was asleep" from the return value alone.
 * Confidence: confirmed via decompiled source (traced m14731D's control flow to the `Ok<Unit>`
 * return site around line ~1778 when no online device is found in the target set).
 */
```

```java
     * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md, "Operational prerequisite:
     * motion sensors must be woken before settings/schedule writes"]
     * This is `writeSchedule` (obfuscated to `B`/`m14729B` - confirmed via the
     * MotionSensorServiceDefault$writeSchedule$1 continuation class's @DebugMetadata(m="writeSchedule")).
     * Same wake-gate pattern as m14731D (writeSettings): calls m14739t()/isOnline() per target
     * device and silently returns `Ok(Unit)` without transmitting SetMotionSensorScheduleCommand if
     * no target device is online. See the class-level note above for the full explanation.
     * Confidence: confirmed via decompiled source.
     */
```

```java
     * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md, "Operational prerequisite:
     * motion sensors must be woken before settings/schedule writes"]
     * This is `writeSettings` (obfuscated to `D`/`m14731D` - confirmed via the
     * MotionSensorServiceDefault$writeSettings$1 continuation class's @DebugMetadata(m="writeSettings")).
     * It calls m14739t()/isOnline() per target device; if none of the target devices are online it
     * skips SetMotionSensorSettingsCommand construction/send entirely and returns the pre-built
     * `Ok(Unit)` singleton (see around line ~1778, label L3a6:
     * `com.github.michaelbull.result.Ok<kotlin.Unit> r2 = com.gelighting.cbygekit.util.UtilsKt.f43576a`)
     * - a silent no-op reported as success, not an error. Sibling method m14732E (`E`) is the
     * path-wide overload with the same pattern.
     * Confidence: confirmed via decompiled source.
     */
```

```java
     * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md, "Operational prerequisite:
     * motion sensors must be woken before settings/schedule writes"]
     * This is the real `isOnline()` (obfuscated to `t`/`m14739t` by JADX/R8 - confirmed by the
     * sibling continuation class MotionSensorServiceDefault$isOnline$1's
     * @DebugMetadata(m="isOnline")). It just resolves DeviceManager.mo13807i()'s
     * StateFlow<AvailabilityState> and checks `instanceof AvailabilityState.Online` - there is no
     * BLE/GATT discoverability scan here. This is the exact status the "wake the device" wizard
     * screen (DeviceSettingsWakeUpFragment) waits on, and the same concept cync-lan already tracks
     * via `bridge.is_online(dev_id)`.
     * Confidence: confirmed via decompiled source.
     */
```


## `com/gelighting/cbygekit/services/scenes/SceneModel.java`

```java
 * [cync-lan reverse-engineering note - see docs/cync_automations.md in the cync-lan repo]
 * A Schedule is internally ALWAYS an implicit Scene under the hood (per
 * ScheduleServiceDefault's create-schedule-* logic) - even a single-device, simple time-trigger
 * Schedule gets its own backing SceneModel. This is why the "fade" feature (f41493h below, a
 * ScheduleFade - see ScheduleFade.java) lives on SceneModel rather than on any Schedule-specific
 * command/model: fade is really a per-scene-slot property, and Schedules just happen to always
 * have a Scene underneath them. ScheduleModel itself (separate class) is just
 * {name, enabled, startTime, Set<ScheduleDay>, sceneIdValue} - a time+day trigger that fires the
 * SceneModel referenced by sceneIdValue.
 * Confidence: confirmed via decompiled source (field position) and ScheduleServiceDefault's
 * create-schedule call graph.
 */
```


## `com/gelighting/cbygekit/services/schedules/ScheduleFade.java`

```java
 * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md ("Scenes control" section) and
 * docs/cync_automations.md in the cync-lan repo]
 * The Schedule "fade" feature (gradual-brightness-transition option in the app's Schedule UI): a
 * 1-byte signed coded duration bucket, not raw seconds. This is a field on SceneModel
 * (see SceneModel.java), not on any Schedule command itself - CreateScheduleHubCommand's payload
 * has no fade byte at all. Every Schedule is internally an implicit SceneModel under the hood
 * (per ScheduleServiceDefault's create-schedule logic), which is why fade lives on the Scene side
 * despite being a Schedule-UI-facing feature. Consumed by AddDeviceSceneCommand, which writes
 * this byte into a per-device scene-slot payload once at scene/schedule-programming time.
 * Confirmed hardware-side, not a software ramp: ExecuteSceneCommand (what a schedule trigger
 * actually fires) never resends color/fade data - the bulb's own firmware executes the fade
 * autonomously using the byte it received once at programming time.
 * Confidence: confirmed via decompiled source (enum values + consumer).
 */
```


## `com/savantsystems/oneapp/control/lighting/RevealFragment.java`

```java
 * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo,
 * "Full light-run-mode incl. MultiColor/MusicShow" section, Reveal follow-up]
 * This is the dedicated Reveal toggle screen. Its onViewCreated (obfuscated `i0`/`mo6640i0`) wires
 * the toggle button's click listener to LightControlViewModel.mo23108p() (renamed from `p`),
 * which sends `Command.SetLightRunMode(LightRunMode.Reveal/Static)` - modeCode 3, the confirmed,
 * already-implemented-in-cync-lan Reveal path (`set_light_effect("reveal")` in devices.py).
 * A second, separate, real code path for activating Reveal exists elsewhere in this same
 * ViewModel: mo23110r() (selecting Reveal via the color-tab picker) sends
 * `Command.SetCombo(true, null, RevealColor, 2)` instead - see LightControlViewModel.java for
 * details. That path is NOT wired into cync-lan and is considered redundant with this one.
 * Confidence: confirmed via decompiled source.
 */
```


## `com/savantsystems/oneapp/control/lighting/viewmodel/LightControlViewModel.java`

```java
     * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo,
     * "Full light-run-mode incl. MultiColor/MusicShow" section, Reveal follow-up]
     * PATH 1 (dedicated Reveal toggle, called by RevealFragment.mo6640i0()'s button click):
     * builds `Command.SetLightRunMode(LightRunMode.Reveal)` - modeCode 3. This is the confirmed,
     * already-implemented path in cync-lan (`devices.py`'s `set_light_effect("reveal")`).
     * See mo23110r() below for PATH 2, a second real but NOT-yet-implemented way to reach the
     * same visual effect via the everyday SetCombo command.
     * Confidence: confirmed via decompiled source.
     */
```

```java
     * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo,
     * "Full light-run-mode incl. MultiColor/MusicShow" section, Reveal follow-up]
     * PATH 2 (color-tab picker selecting Reveal): when `color` is the singleton `RevealColor`
     * object (see the `Intrinsics.areEqual(color, RevealColor.f88998a)` check a few lines below),
     * this sends `Command.SetCombo(true, null, RevealColor, 2)` - the everyday brightness/color
     * command (op 0xF0), not SetLightRunMode. Per SetComboCommand.java, RevealColor serializes to
     * a 2-byte `[0xFF, 0xF0]` color-type sentinel in place of the usual 4-byte CCT/RGB shape, and
     * is explicitly REJECTED for hub-relayed devices ("RevealColor is not supported by hubs") -
     * only works over direct XLink mesh. RevealColor itself carries no Full-Color-vs-Soft-White
     * distinction; that's purely the bulb's SKU/firmware (see ProductModel.java's Reveal SKU
     * entries), not a wire field. This path produces the same end-user effect as PATH 1
     * (mo23108p() above, the dedicated Reveal toggle -> SetLightRunMode modeCode 3, already wired
     * in cync-lan) and is NOT implemented in cync-lan - considered redundant with PATH 1.
     * Confidence: confirmed via decompiled source.
     */
```


## `com/savantsystems/oneapp/devices/settings/DeviceSettingsWakeUpFragment.java`

```java
 * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo,
 * "Operational prerequisite: motion sensors must be woken before settings/schedule writes"]
 * This is the "wake up your device" screen shown before motion-sensor/wireless-switch/remote
 * settings or schedule edits (device classification 15=WirelessSwitch, 17=Remote,
 * 18=MotionSensor - see WhenMappings below and m24493B0()). Exact user-facing copy
 * (`strings.xml:2354`, resource `wake_up_wire_free_device_body`):
 *   "Press and hold the off button for 5 seconds to wake the device & make it discoverable.
 *   The LED light will turn green. Make sure your phone is within 40 feet of the device during
 *   this process."
 * "Discoverable" here is misleading marketing copy - it does NOT trigger any BLE/GATT scan.
 * The screen's own gating (m24493B0(), line ~128: button enabled = `!DeviceExtensionsKt.
 * m24957n(device)`, which is true only once the device's ConnectionState flips to
 * ConnectionState.Connected/f89975c) just reactively watches the ordinary Device Flow supplied
 * by DeviceSettingsWakeUpViewModel/ObserveDeviceUseCase - the same online/offline status
 * exposed elsewhere as DeviceManager.mo13807i()'s AvailabilityState StateFlow. No dedicated
 * discovery/scan routine runs here.
 * Don't confuse with commissioning's separate "setup mode" copy (`strings.xml:1271-1275`,
 * blinking BLUE, shown only during first-time device add) - different flow, different LED color.
 * Confidence: confirmed via decompiled source.
 */
```


## `com/savantsystems/oneapp/domain/colors/model/LightRunMode.java`

```java
 * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo,
 * "Full light-run-mode incl. MultiColor/MusicShow" section]
 * This is the app-domain-layer LightRunMode model (used directly by LightControlViewModel.p(),
 * the dedicated Reveal toggle path) - a UI-facing sealed class with no wire modeCode ints. For
 * the actual protocol modeCode table (0=Static/1=LightShow/2=MusicShow/3=Reveal/4=MultiColor)
 * see the separate `com.gelighting.cbygekit.services.devices.model.LightRunMode` class, which
 * IS what SetLightRunModeCommand serializes onto the wire.
 * Confidence: confirmed via decompiled source.
 */
```


## `com/thingclips/smart/android/network/http/pin/ThingCertificatePinner.java`

```java
 * [cync-lan reverse-engineering note - see docs/ble_provisioning_protocol.md in the cync-lan repo]
 * Peripheral to BLE provisioning, and possibly not even Cync-specific: generic HTTPS certificate
 * pinning (via okhttp3.CertificatePinner) for the "ThingClips" (com.thingclips.smart.android.network)
 * cloud SDK bundled in this app - a different vendor package family than Cync's own xlink.cn cloud
 * API (see XlinkAgentManager's note). Its role in the BLE provisioning flow specifically appears to
 * be minimal-to-none; not otherwise examined by this research pass.
 * Confidence: Not covered by the BLE provisioning doc's research pass - role inferred from package/imports only.
 */
```


## `io/xlink/wifi/sdk/tcp/TcpPacketWriter.java`

```java
 * [cync-lan reverse-engineering note - see docs/mesh_opcodes.md in the cync-lan repo]
 * The @Deprecated tag here is cited in the doc's "TCP relay envelope research" section as
 * evidence that the whole io.xlink.wifi.sdk/xlink.legacy pathway (including Xlink.a()'s
 * HDLC/PPP-style frame builder, reached via XlinkDeviceManager.CommandDelegate.g()/h()) is very
 * likely the phone app's OLDER command channel - plausible but not proven to be byte-identical
 * to the device-facing protocol cync-lan's own TCP relay replicates.
 * Confidence: confirmed via decompiled source (the annotation itself); plausible, not
 * independently confirmed (what it implies about wire-format equivalence)
 */
```


## `io/xlink/wifi/sdk/util/XlinkDTSLUtils.java`

```java
 * [cync-lan reverse-engineering note - see XLINKDTSL_FINDINGS.md in
 * sources/com/gelighting/cbygekit/foundation/wifi/ in this tree]
 * System.loadLibrary("xlinkdtsl") below pulls in libxlinkdtsl.so, which is stock Eclipse tinyDTLS
 * (symbol-for-symbol match: dtls_new_peer/dtls_encrypt/dtls_ccm_encrypt_message/etc., plus bundled
 * rijndael (AES) and SHA-256 reference code) wrapped in a thin JNI shim, providing per-peer
 * DTLS 1.2 (TLS_PSK_WITH_AES_128_CCM_8) encrypt/decrypt for this class's UDP send/receive path.
 * NOT relevant to cync-lan's TCP mesh-relay protocol (port 23779, docs/mesh_opcodes.md): that
 * connection uses plain javax.net.ssl SSLSocket (XlinkTcpService.java), never this library. This
 * DTLS/UDP layer is also disabled in current app builds - XlinkAgentManager.java forces
 * setUseDTLS(false) at init, XlinkAgent.initDTLS() only ever calls initDTSL(null, null, null), and
 * every UDP path gates on the (always-false) getUseDTLS() flag before ever reaching this class.
 * Confidence: high on library identity/disabled-state, moderate-high on the "legacy/vestigial"
 * historical-purpose read - see findings doc for full reasoning and citations.
 */
```
