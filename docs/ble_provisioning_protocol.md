# BLE GATT provisioning protocol (Telink mesh)

This documents the actual Bluetooth Low Energy wire protocol the Cync/C-by-GE app speaks to
commission (pair/provision) a brand-new device - one level below
[cloud_independence_research.md](cloud_independence_research.md), which established the
commissioning *flow* (what data gets decided, in what order, and that only one step ever touches
the cloud) without touching the actual BLE bytes. This doc is that missing layer: service/
characteristic UUIDs, the pairing/encryption handshake, command framing, and the WiFi-credential
handoff format - everything a real client implementation (e.g. a Python `bleak`-based one) would
need.

Sourcing conventions match [mesh_opcodes.md](mesh_opcodes.md): **confirmed** (cited to an exact
decompiled-app class/line), **plausible** (reasonable inference, not directly proven), **not found
/ blocked** (explicitly flagged as absent). Research credited to a background agent run 2026-07-17
against `/Users/proxy-alt/Downloads/cync_decompiled_v2/` (a re-decompile with anti-inlining flags -
see [cloud_independence_research.md](cloud_independence_research.md) for why the original decompile
needed redoing), cross-validated against independent prior art (below).

## Independent cross-validation - now against real, working, cloned source

**Update**: `google/python-laurel` and `google/python-dimond` were cloned and read in full (not
just found via web search) - see below for a precise diff against the decompile-based findings.
`python-laurel`'s own `laurel/__init__.py` (201 lines, fully read) confirms it's genuinely
Cync/GE-specific (`README.md`: "C by GE Bluetooth lightbulbs"; auth endpoint
`https://api2.xlink.cn/v2/user_auth`, `corp_id="1007d2ad150c4000"` - real Cync cloud API constants)
but is only a thin cloud-bootstrapping wrapper - it authenticates, pulls mesh name/password + device
MAC/ID list from Cync's cloud, then does `import dimond` and hands off **all** actual BLE
communication to `python-dimond`. So the real protocol comparison is against `dimond/__init__.py`
(176 lines, fully read, Apache-2.0, "derived from python-tikteck").

The single strongest signal in this whole investigation: `python-dimond`'s hardcoded UUIDs
(`dimond/__init__.py:114-116`) are **byte-for-byte identical** to the decompiled app's -
`00010203-0405-0607-0809-0a0b0c0d1911` (notify), `...1912` (control/command write), `...1914`
(pairing). Two unrelated reverse-engineering efforts (a 2018 Google-internal community project with
no access to Cync's APK, and this session's direct decompile) converging on identical byte values is
about as strong as evidence gets that this is standard Telink Mesh SDK behavior, not something
Cync-specific or a decompile artifact. `vpaeder/telinkpp` is a second implementation validating the
same scheme (not cloned this pass). `google/python-laurel` is cited by an
existing, unrelated project (`juanboro/cync2mqtt`) as the Telink-mesh library it uses **specifically
for Cync/C-by-GE devices** - the closest thing available to independent confirmation that Cync
hardware speaks unmodified Telink mesh. None of this prior art was cloned/read directly this
session (found via web search only) - worth doing before writing a real implementation.

## GATT service and characteristics

Confirmed via `com/gelighting/cbygekit/foundation/commands/Telink.java`, referenced from
`TelinkDeviceBleManager`'s Nordic `BleManager` service-discovery callback
(`TelinkDeviceBleManager.java` ~lines 1610-1659):

| Constant | UUID | Role |
|---|---|---|
| `Telink.f28872f` | `00010203-0405-0607-0809-0a0b0c0d1910` | **Primary GATT service** |
| `Telink.f28876j` | `...1914` | **Pairing characteristic** - auth handshake, mesh-credential handoff |
| `Telink.f28873g` | `...1911` | **Status characteristic** - notify, subscribed at manager setup |
| `Telink.f28874h` | `...1912` | **Command characteristic** - write path for all encrypted mesh commands |
| `Telink.f28875i` | `...1913` | **OTA characteristic** - firmware update writes |
| `Telink.f28868b` / `f28870d` | `0000180a-...` / `00002a26-...` | Standard BLE Device Information Service / Firmware Revision String - not Telink-specific |
| `Telink.f28869c` | `19200d0c-0b0a-0908-0706-050403020100` | Checked in a separate callback path, near-mirror (byte-reversed form) of the primary service UUID - role **not confirmed** |
| `Telink.f28871e` | `00002af0-...` | Characteristic under `f28869c` - not a standard SIG UUID, **purpose not confirmed**, no other reference found |

Also present: a `Telink.OPCODE` enum (`PAIR_REQ`, `PAIR_RSP`, `PAIR_REJECT`, `PAIR_NETWORK_NAME`,
`PAIR_PASS`, `PAIR_LTK`, `PAIR_CONFIRM`, `PAIR_LTK_REQ`, `PAIR_LTK_RSP`, `PAIR_DELETE`,
`PAIR_DEL_RSP`, `ENC_REQ`, `ENC_RSP`, `ENC_FAIL` - `Telink.java:90-96`) matching standard public
Telink BLE-Mesh SDK naming - another point of convergence with known Telink behavior. **Caveat**:
actual wire writes use literal integer opcodes, not this enum, so treat it as documentation of
intent rather than a direct byte-value table.

## Pairing handshake and session-key derivation

**Corrected against `python-dimond`'s real, working implementation** (`dimond/__init__.py:112-133`,
`connect()`) - this supersedes several specifics from the original decompile-only pass below, which
mis-attributed which byte buffer plays which role. Confirmed exact algorithm, from real source:

```python
def key_encrypt(name, password, key):          # dimond/__init__.py:45-49
  data = XOR(pad16(name), pad16(password))      # 16 bytes
  return encrypt(key, data)                     # AES-ECB(key, data)

def generate_sk(name, password, data1, data2):  # dimond/__init__.py:37-43
  key = XOR(pad16(name), pad16(password))       # 16 bytes
  data = data1[0:8] + data2[0:8]                # 16 bytes
  return encrypt(key, data)                     # AES-ECB(key, data)
```

1. Phone generates 8 **local random bytes** (`R_app`, via `get_random_bytes(8)`) - zero-padded to
   16 for use as an AES key.
2. Phone writes to the **pairing characteristic** (`...1914`): `[0x0C] + R_app[0:8] +
   key_encrypt(meshName, meshPass, key=R_app_padded16)[0:8]` - 17 bytes total
   (`dimond/__init__.py:118-127`). **Correction**: the original decompile-based pass described the
   AES key here as a fixed constant (`{0xA0..0xA7, 0×8}`, read from `Telink.f28877k`) - the real,
   validated implementation uses the just-generated *local random bytes* as the key instead, with
   `XOR(name, password)` as the plaintext (the two decompiled roles were swapped). The decompiled
   `f28877k` constant may be a class-level placeholder/initializer that gets overwritten with real
   `SecureRandom` output before use, rather than a genuine fixed value - not fully resolved, but
   `python-dimond`'s account is the one with real-world validation behind it.
3. Phone reads the pairing characteristic for the device's response and takes bytes `[1:9]` as
   `R_dev`, the device's own random contribution (`dimond/__init__.py:129,133`). **`python-dimond`
   itself performs no mutual-auth verification** - it just trusts whatever the device returns and
   proceeds. **Update, resolved**: a direct read of the real Cync app's own callback
   (`C2184d.java`'s `DataReceivedCallback`, registered on this exact `ReadRequest` in
   `TelinkDeviceBleManager.m14334v`) confirms the real app DOES perform this verification -
   see "Resolved: the real app's mutual-auth check" below. The earlier "unresolved, either the app
   checks and python-dimond skips it, or the decompile mis-read something" framing is now settled:
   it's the former. This doesn't change whether a from-scratch client's pairing attempt succeeds
   (the device doesn't care whether the phone verifies its own response), but it does mean the
   check is real, confirmed protocol behavior worth replicating as a diagnostic.
4. Both sides derive **`sessionKey = AES_ECB(key=XOR(meshName, meshPass), data=R_app[0:8] ‖
   R_dev[0:8])`** (`dimond/__init__.py:133`, calling `generate_sk`). The `XOR(meshName, meshPass)`
   key-material claim from the original pass is **confirmed exactly** - only the identity of the two
   8-byte data halves needed correcting (both are randomly-generated-this-session values, not a
   fixed constant).
5. The Telink AES primitive itself (`encrypt()`, `dimond/__init__.py:29-35`) does exactly the
   byte-reversal quirk the original decompile pass flagged: `AES.new(bytes(reversed(key)),
   MODE_ECB).encrypt(bytes(reversed(data)))`, then the result is reversed back too. **Confirmed
   exactly** - this is real, load-bearing Telink SDK behavior, not a decompiler artifact.

`python-dimond`/`python-laurel` only ever *join* an already-provisioned mesh (a device the Cync
cloud already knows about) - they don't implement new-mesh creation or WiFi handoff, so they provide
**no cross-validation** for `pairMesh$2.java`'s "hand the device its permanent mesh credentials"
step or the `SetWifiCommand` chunking scheme below - those remain sourced from the decompile alone.

### Resolved: the real Cync app's `R_app` is a fixed constant, not random - and the exact factory-bootstrap bytes are now confirmed

A follow-up pass through the real Cync app's own `TelinkDeviceBleManager.m14334v` ("authenticate")
and `Telink.java`'s static initializer closes the "not fully resolved" caveat on step 2 above.
**The real app's `R_app` is genuinely a fixed constant, not `SecureRandom` output** -
`Telink.f28877k` is a `final` field, assigned once (`{0xA0,0xA1,0xA2,0xA3,0xA4,0xA5,0xA6,0xA7,
0,0,0,0,0,0,0,0}`), and used as-is at two independent real call sites: the initial pairing write
(`m14334v`) and the read-response callback that reconstructs the same value to derive the session
key (`C2184d.mo14353a`). `python-dimond` generating fresh random bytes each session is a difference
between it and the real app, not a sign the real app also randomizes - both are protocol-compatible
since the encryption algorithm doesn't require `R_app` to be unique per session, just known to both
sides deriving the same session key.

`m14334v` has two branches:
- **name/password exactly the Telink factory defaults** (`"telink_mesh1"`/`"123"`) - the
  brand-new/never-provisioned-device case: writes a fully pre-baked 17-byte constant
  (`Telink.f28878l`) verbatim, no computation needed.
- **otherwise** (re-authenticating against an already-known mesh): computes
  `[0x0C] + R_app[0:8] + key_encrypt(name, password, key=pad16(R_app))[0:8]` - the same general
  formula step 2 above already described, just with the fixed `R_app` instead of a random one.

Exact confirmed byte values (`Telink.java`'s static initializer):

```
R_APP (Telink.f28877k[0:8])        = A0 A1 A2 A3 A4 A5 A6 A7
FACTORY_DEFAULT_PAIRING_WRITE       = 0C A0 A1 A2 A3 A4 A5 A6 A7 8D B6 74 71 1B 85 5A 79
  (Telink.f28878l - opcode 0x0C + R_APP + key_encrypt("telink_mesh1","123",key=pad16(R_APP))[0:8])
DEFAULT_LTK (Telink.f28879m)        = C0 C1 C2 C3 C4 C5 C6 C7 D8 D9 DA DB DC DD DE DF
```

**Independent confirmation, not just a decompiled literal**: `src/cync_lan/ble_provision.py`'s
`build_pairing_write("telink_mesh1", "123")` - implementing the general formula above from scratch,
using the `cryptography` package's AES-ECB primitive - reproduces `FACTORY_DEFAULT_PAIRING_WRITE`
exactly (see `test_build_pairing_write_reproduces_the_factory_default_constant` in
`tests/components/cync_lan/test_ble_provision.py`). This is real evidence the crypto
implementation (byte-reversal quirk, XOR key derivation, padding) is correct, not just internally
consistent with itself.

The mesh-credential-handoff opcode bytes (`TelinkDeviceBleManager$pairMesh$2.java`) are also now
fully confirmed by direct read: `4`=NAME, `5`=PASSWORD, `6`=LTK, each written as
`[opcode] + AES_ECB(sessionKey, pad16(value))[0:8]`, zero-padded to 17 bytes - matching this doc's
existing "genuinely open" `OPCODE` enum note (`ordinal+1` for `PAIR_NETWORK_NAME`/`PAIR_PASS`/
`PAIR_LTK` = literals 4/5/6) exactly.

**Practical upshot**: a from-scratch client provisioning a brand-new device never needs to touch
`SecureRandom` at all for the bootstrap step - `FACTORY_DEFAULT_PAIRING_WRITE` is a fixed constant
that works for every never-provisioned Telink device, confirmed both from the decompiled source and
by independently reproducing it from the documented formula.

**Shipped, EXPERIMENTAL, untested against real hardware**: `src/cync_lan/ble_provision.py`
implements the full flow above (`bleak`-based scan → connect → factory-bootstrap pairing write →
session-key derivation → target mesh name/password/LTK handoff), exposed as a `cync-lan-ble-provision`
CLI (`pip install cync_lan[ble]`). Does not yet implement the WiFi credential handoff
(`SetWifiCommand`) below - only the BLE mesh-join step.

### Resolved: the real app's mutual-auth check, and the pairMesh confirmation byte

A direct read of two real callback classes - not just `python-dimond`'s account, which only
establishes what a *minimal* client needs to do - closes two more of this doc's own open items.

**The real Cync app DOES verify the device's pairing response** (`C2184d.java`'s
`DataReceivedCallback`, the callback registered on the `ReadRequest` right after
`TelinkDeviceBleManager.m14334v`'s pairing write). Contrary to this doc's earlier framing (based
only on `python-dimond`, which skips this), the shipped app reconstructs an expected proof value
from the device's own `R_dev` and compares it against what the device actually sent:

```
r_dev = response[1:9]
expected_proof = key_encrypt(meshName, meshPass, key=pad16(r_dev))[0:8]
# real app requires: response[9:17] == expected_proof, else session key is set to null (pairing fails)
```

This is a **client-side-only** check - the device has no way to know whether the phone validated
its response, so this cannot be what makes a device accept or reject pairing. A from-scratch client
that skips it (as `python-dimond` does, and still works) will not be rejected by the device for
that reason. It's valuable anyway as a diagnostic: if this check fails, something about how `R_dev`
or the mesh name/password is being interpreted is probably wrong, and would otherwise surface later
as a much more confusing failure once the (silently-wrong) session key gets used.

**Shipped**: `src/cync_lan/ble_provision.py`'s `verify_pairing_response()` implements this exact
check, called as a non-fatal diagnostic (logged as a warning, not raised) in `provision_device()`.

**The `pairMesh` confirmation read must check for the literal byte value `7`, not merely
"nonzero"** (`C2185e.java`'s `DataReceivedCallback`, registered on the final confirmation
`ReadRequest` in `pairMesh$2.java`): the callback only sets its success flag when
`response[0] == 7`; any other value - including `0`, and including other plausible-looking nonzero
values - leaves it `false` ("not confirmed"). This lines up neatly with the same "literal =
ordinal+1" pattern already confirmed elsewhere in this doc for the NAME/PASSWORD/LTK opcodes
(`4`/`5`/`6` = enum ordinals `3`/`4`/`5`) - enum ordinal `6` is `PAIR_CONFIRM`, and `6+1=7`, a
semantically sensible match for "pairing confirmed" under that same hypothesis.

An earlier version of `ble_provision.py` checked `response[0] != 0` here instead of
`response[0] != PAIR_CONFIRM_BYTE` (`7`) - a real bug (not merely an unconfirmed guess), since it
would have treated most rejection responses as success. Caught and fixed by re-reading `C2185e.java`
directly rather than assuming; see `test_provision_device_raises_for_nonzero_but_wrong_confirmation_byte`
in `tests/components/cync_lan/test_ble_provision.py` for the regression test.

## Command encryption (post-pairing)

**Corrected against `python-dimond`'s real implementation** (`dimond/__init__.py:51-72,
150-161` - `encrypt_packet`/`send_packet`). The original decompile-only pass got the general
*shape* right (MIC + keystream, both AES-ECB-derived from the session key and a MAC-derived nonce)
but the exact packet layout was wrong in several places - corrected below, all cited to real,
working code:

**Real 20-byte packet layout** (`send_packet`, `dimond/__init__.py:150-161`):

```
byte    0-1   seq (little-endian, random start, incremented per command, wraps at 65535)
byte    2     unused (always 0x00 for outbound commands)
byte    3-4   MIC (written by encrypt_packet, see below)
byte    5-6   target MeshAddress (little-endian)
byte    7     command/opcode
byte    8-9   vendor ID (little-endian) - see cross-validation note below
byte   10-19  payload (up to 10 bytes for a single-packet command)
```

This differs from the original decompile-based description in three ways: target address comes
*before* the opcode byte, not after; there's a **2-byte vendor-ID field the original pass missed
entirely** (see below - this is the most valuable correction, since it directly explains something
already in cync-lan's own code); and payload starts at byte 10, not immediately after target.

**MIC and keystream** (`encrypt_packet`, `dimond/__init__.py:51-72`) - both are **single AES-ECB
block operations**, not a multi-block/incrementing-counter scheme as the original pass described:

```python
auth_nonce = macdata[0:4] + [0x01] + packet[0:3] + [15] + [0]*7   # 16 bytes
authenticator = AES_ECB(sk, auth_nonce)
authenticator[0:15] ^= packet[5:20]                                # fold in the unencrypted packet
mac = AES_ECB(sk, authenticator)
packet[3], packet[4] = mac[0], mac[1]                              # 2-byte MIC written in-place

iv = [0] + macdata[0:4] + [0x01] + packet[0:3] + [0]*7             # 16 bytes
keystream = AES_ECB(sk, iv)
packet[5:20] ^= keystream[0:15]                                    # encrypt in-place, 1 block covers the whole 15-byte target+opcode+vendor+payload region
```

`macdata` is the device's MAC address, byte-reversed once at object construction
(`dimond/__init__.py:107`) and reused as-is here - functionally the same "reversed MAC" the original
pass described, just computed earlier rather than inline. The nonce/IV buffers are full 16-byte AES
blocks (not 8 bytes as originally stated) - the extra length is fixed zero-padding, since AES-ECB
always operates on 16-byte blocks. Because a command's encrypted region (target+opcode+vendor+
payload, bytes 5-19) is capped at 15 bytes - one AES block's worth - a single keystream block
suffices; no incrementing counter is needed *within* one packet. The original decompile pass likely
over-read a loop structure that only ever executes once at this payload size, or conflated this with
separate handling for larger multi-block writes (e.g. firmware OTA, a genuinely different
characteristic/path this research didn't examine).

**Cross-validation - this connects directly to cync-lan's own existing, already-shipping code**: the
constructor call in `python-laurel` passes a hardcoded vendor ID, `dimond.dimond(0x0211, ...)`
(`laurel/__init__.py:126`), which becomes `packet[8]=0x11, packet[9]=0x02` (little-endian). Every
mesh command payload cync-lan's own `src/cync_lan/devices.py` builds - `set_power`:
`[0x11,0x02,state,0x00,0x00]`, `set_brightness`: `[0x11,0x02,0x01,bri,...]`, and every other
confirmed command in `mesh_opcodes.md` - starts with exactly these same two bytes, previously
documented only as an unexplained "VendorID" prefix. This confirms it precisely: cync-lan's TCP
relay payloads are the **same base Telink mesh command format** `python-dimond` sends directly over
BLE, just wrapped inside cync-lan's own outer TCP envelope (`msg_id`/`op_code`/`cmd_code` framing)
instead of a raw 20-byte BLE packet. The `op_code` byte cync-lan already uses for e.g. `set_power`
(`0xD0`) is the literal same byte as `python-dimond`'s BLE-native `command` field for the same
operation - both transports carry the identical underlying mesh command, cync-lan's TCP relay is
just a different wire wrapper around it.

**Notification/status parsing, confirmed working**: `python-laurel`'s `callback()`
(`laurel/__init__.py:67-86`) filters incoming notifications on `data[7] == 0xdc` (matches
`mesh_opcodes.md`'s already-documented `MESH_STATUS` handling), then reads device states from
`data[10:18]` in 4-byte groups (id, ?, brightness-or-flag, color/temp byte) - a real, working,
smaller cousin of the already-documented 24-byte MeshInfo entry struct, additional confirmation the
overall notification framing lines up with what cync-lan's TCP-side code already expects.

Real inter-command throttling still applies per the original decompile finding - `20ms` for a group
address / `320ms` otherwise (`$writeCommand$2.java:222`) - `python-dimond` itself has no explicit
delay of this kind, so this specific detail remains decompile-only, unconfirmed against working
code.

## Notifications (device → app)

Confirmed, `NotificationType.java` (~lines 38-56): a single leading type byte on the **status
characteristic** (`...1911`) selects how the rest of the payload gets parsed:

| Byte | Type |
|---|---|
| `0xE1` | ADDRESS |
| `0xC8` | DEVICE_TYPE_AND_VERSION |
| `0xD4` | GROUP |
| `0xC1` | SCENE |
| `0xE7` | AUTOMATION |
| `0xDC` | MESH_STATUS |
| `0xF6` | WIFI (has its own sub-discriminator, see below) |
| `0xE9` | QUERY_TIME |
| `0xEA` | BASE_EA |
| `0xEB` | RGB_STATUS |

`WIFI` (`0xF6`) notifications carry a second discriminator byte
(`TelinkWifiMultipartNotification.Subtype`, ~lines 54-59): `WIFI_LIST=0x81`,
`SET_WIFI_RESPONSE=0x82`, `WIFI_CONNECTION_STATUS_ROUTER=0x83`, `WIFI_CONNECTION_STATUS=0x84`.
Three of these are multipart (reassembled by the `*Joiner` classes) - **this multipart mechanism is
device-to-app only** (e.g. a long WiFi network scan list coming back from the device), not used for
the outbound `SetWifiCommand` below, which has its own, separate chunking scheme.

## WiFi credential handoff (`SetWifiCommand`)

Confirmed, `SetWifiCommand.java` + `Utilities.m13402k`/`Utilities$chunkByteArray$1.java` - **the one
piece of this protocol with no prior art found anywhere** (see cross-validation section above); this
is Cync/GE-specific, layered on top of the generic Telink base protocol.

**Confirmed to use the identical encrypted mesh-command path as every other command** - re-verified
2026-07-17 against the corrected packet model above. `DeviceWifiConnectionManager.m13937d`
constructs `SetWifiCommand` and dispatches it via `DeviceController.mo14149i`
(`DeviceWifiConnectionManager.java:891,1016`) - the same entry point every other command uses, down
through `TelinkDeviceBleManager.m14326L` ("writeCommand"), the exact MIC/keystream routine already
documented above. **No cleartext WiFi credentials are ever sent.**

Opcode array `f34989z = {0xF6, 0x11, 0x02, 0x02}` (`SetWifiCommand.java:56`). Inner plaintext
payload before chunking (`Utilities.java:225-236`):

```
[totalChunks(1B) = ceil((len(ssid) + len(pass) + 5) / 8)]
[len(ssid)(1B)][ssid, UTF-8]
[len(pass)(1B)][pass, UTF-8]
[0x01]
[deviceType.id(1B)]
```

This buffer is split into **8-byte chunks**, each prefixed with a **1-based running index byte**
(`Utilities$chunkByteArray$1.java:32-34`).

**Corrected chunk→packet mapping** (`TelinkDeviceBleManager.java:1361-1375`) - the chunk index does
**not** occupy the opcode byte position as originally stated; it goes into **packet byte 2**, the
position documented above as "unused, always `0x00`" for ordinary commands - `SetWifiCommand`
repurposes it:

```
packet[0:2]   seq (random, as usual)
packet[2]     chunk index (1-based) - repurposes the "unused" byte, NOT the opcode position
packet[3:5]   MIC (as usual)
packet[5:7]   target MeshAddress, byte-reversed = 0x0000 (SELF_ADDRESS - see below)
packet[7]     0xF6 - fixed opcode for every chunk (matches the WIFI notification type byte)
packet[8:10]  0x11, 0x02 - vendor ID 0x0211, same as every other command
packet[10]    0x02 - WiFi-family sub-discriminator (f34989z's 4th byte)
packet[11]    chunk index again (redundant with packet[2])
packet[12:20] up to 8 bytes of this chunk's actual payload
```

commandBody (packet[7:20]) is exactly 13 bytes - 1 sub-opcode + 1 redundant chunk-index + 8 data
bytes - filling the corrected 10-byte-payload-capacity model precisely once the fixed opcode/vendor
bytes are accounted for. WiFi credential chunking happens *above* the encryption layer, as N
separate fully-encrypted mesh commands sent in sequence - not one long GATT write, and not a special
unencrypted framing.

**Target address**: for a freshly-discovered, not-yet-provisioned device, `TelinkBleDeviceController.
m14192x` resolves a null target to `MeshAddress.Companion.m13644c(deviceId.index)`; a fresh device's
`DeviceId` has index 0, so this resolves to `MeshAddress.f31063g` = `MeshAddress(0)` - confirmed as
the SELF_ADDRESS sentinel (`MeshAddress.java:110`, `TelinkBleDeviceController.java:1001-1057`).

**The step-1-vs-step-3 ordering question, resolved**: `writeCommand` itself calls "await BLE
connected" (`TelinkDeviceBleManager.java:1388`) then "await/derive session key"
(`TelinkDeviceBleManager.java:1399`) before encrypting *any* packet - and the underlying BLE connect
call is idempotent (`TelinkDeviceBleManager.java:1825`: only connects if not already connected). So
the 20-step pipeline's step 1 (`setWifiCredentialsIfNeeded`)'s first command transparently forces the
full connect+pair+session-key sequence on demand; step 3's explicit `connectAndPairOperation` later
in the list is a redundant confirmation of an already-established connection, not a first-time
pairing step. The step list's linear ordering was misleading about when BLE connection actually
happens - the real implementation is defensive/idempotent about it at every command, not just once
up front.

## New-device discovery (before any of the above)

Confirmed, 2026-07-17 pass across the ge-sdk library and the app's own UI/domain layer
(`com/savantsystems/oneapp/commissioning/scan/`, `.../domain/commissioning/ScanDevicesUseCase.java`,
`.../data/commissioning/ge/GECommissioningDataSource.java`, and
`com/gelighting/cbygekit/services/devices/BleDeviceScanner.java`). This is the step before
everything above - how the app finds a pairable device in the first place.

**No BLE-level `ScanFilter` is used at all**: `BleDeviceScanner$findDevices$1.java:360` builds an
empty `new ScanFilter.Builder().build()` - the OS-level scan (`BluetoothLeScanner.startScan`) sees
every nearby BLE advertisement. **All filtering happens in application code afterward**
(`BleDeviceScanner.m13735b`/`parseScanResult`, `BleDeviceScanner.java:216-745`):

1. Reads BLE manufacturer-specific data keyed by company ID `0x0211` (`BleDeviceScanner.java:166-174,
   326`) - the same `0x11,0x02` vendor-ID bytes already confirmed in the mesh command protocol above;
   `0x0211` is Telink Semiconductor's registered Bluetooth SIG company ID.
2. Within that payload, reads a device-type byte, validated against the full GE/Cync product catalog
   (`DeviceType.java`) - an advertisement with an unrecognized type byte is rejected outright
   (`"Unknown device type"`, `BleDeviceScanner.java:372`).
3. Separately checks the advertised local BLE name against `"telink_mesh1"`
   (`BleDeviceScanner.java:457`) - Telink's stock factory-default/unprovisioned-node name.
   `DeviceScanRecord.isNewDevice` (`model/DeviceScanRecord.java:30`) is set true when the name
   matches this default, or the device's MAC is already tracked as mid-commissioning locally.

**The one non-local piece**: distinguishing "a device I already own" from "an unowned device of the
same product type" depends on a locally-cached set of already-known device/mesh names
(`MeshDataProvider.mo13910c`, backed by `DeviceServiceDefault.java:865-867`'s `StateFlow<Set<String>>`,
itself derived from `locationService` state populated at login) - **not a live network call at scan
time**, but the exclusion list itself originates from the cloud-synced device list. This is not a
new blocker for cync-lan specifically - the integration already maintains an equivalent device list
from its own cloud export.

**Practical takeaway for a Python/HA implementation**: no special BLE scan filter needed - filter
client-side on manufacturer-data company ID `0x0211`, parse the device-type byte against a known
catalog, and treat the advertised name `"telink_mesh1"` (or any name not matching an already-owned
device) as the "ready to pair" signal. No cloud call required at discovery time itself.

## Confidence summary and open items

**Highest confidence - validated against real, working, cloned code (`python-dimond`)**: service/
characteristic UUIDs, the full session-key handshake algorithm (with the two corrections above), the
exact 20-byte command packet layout including the previously-missing vendor-ID field, the MIC +
single-block-keystream encryption algorithm, and basic status-notification parsing
(`data[7]==0xdc`, `data[10:18]`). The vendor-ID discovery also retroactively confirms cync-lan's own
existing `0x11,0x02` payload-prefix convention is this exact same field, not a coincidence - and the
same bytes turn up a third time as the BLE-advertisement manufacturer-data company ID used for
new-device discovery.

**High confidence, decompile-only but now internally cross-checked and fully traced end-to-end**:
the notification type-byte table, `pairMesh$2.java`'s mesh-credential-handoff step, the
`SetWifiCommand` chunk/payload layout (re-verified 2026-07-17 against the corrected packet model,
including the connect-on-demand mechanism resolving the earlier step-ordering question and
confirming no cleartext credentials), new-device BLE discovery/filtering, and the 20/320ms
inter-command throttling. `python-dimond`/`python-laurel` only ever join an *already-provisioned*
mesh, so none of the provisioning-specific pieces could be cross-checked against independent working
code this pass - but the decompile-internal consistency (the same call paths, the same encryption
routine, the same vendor-ID bytes appearing in three unrelated places) is itself strong internal
evidence.

**Resolved this pass** (moved out of "genuinely open" below): whether the Cync app performs the
mutual-auth verification step - **yes**, confirmed via direct read of `C2184d.java` (see "Resolved:
the real app's mutual-auth check" above), along with the exact `pairMesh` confirmation byte value
(`7`, not merely "nonzero" - a real bug this caught and fixed in `ble_provision.py`).

**Lower confidence / genuinely open**:
- Exact semantic mapping of the `Telink.OPCODE` enum's ordinal values to the literal integer opcodes
  actually used in wire writes (code uses literals, not the enum, at the actual write sites) - the
  "ordinal+1" hypothesis now has 5 confirmed data points (opcodes 4/5/6/7 for
  NAME/PASSWORD/LTK/CONFIRM = ordinals 3/4/5/6, plus the original 11=ENC_REQ), still not exhaustively
  proven for the remaining ordinals.
- `f28869c`/`f28871e` (the byte-reversed service/characteristic pair) - purpose not determined.
- Whether an already-provisioned device stops advertising its default name (plausible, not directly
  confirmed via UI strings this pass) - relevant only for a "should already-paired devices ever show
  up in a scan" edge case, not a correctness blocker.
- `ble_provision.py`'s new-device scan filter doesn't validate the device-type byte the real app
  checks (`BleDeviceScanner.java`) - only manufacturer-data company ID + advertised name. Looser
  than the real app's filter, not incorrect, but could surface non-Cync Telink devices in scan
  results if any happen to be nearby.

**Recommended next steps, in order of cost**: (1) clone `vpaeder/telinkpp` too, as a second
cross-check on the command-encryption algorithm now confirmed above; (2) ~~an actual prototype
attempt against real hardware is now reasonable to try directly~~ - **done**: `src/cync_lan/ble_provision.py`
implements discovery + the factory-bootstrap pairing + mesh-credential handoff (not yet WiFi
handoff), awaiting a real-hardware test result; (3) a live BLE capture during a real pairing session
remains the way to get full certainty on the couple of remaining lower-confidence items above, same
as `docs/cloud_independence_research.md`'s original recommendation - now scoped to just the
provisioning-specific pieces rather than the whole protocol.
