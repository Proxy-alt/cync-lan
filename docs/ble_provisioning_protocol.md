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

## Independent cross-validation - high confidence

Before trusting any of this, the single strongest signal: a **completely independent** open-source
project, [`google/python-dimond`](https://github.com/google/python-dimond) (a Python client for
Telink's BLE-mesh protocol generally, not Cync-specific), landed on the **exact same characteristic
UUIDs** as this session's APK decompile - notify `...1911`, command-write `...1912`, pairing
`...1914`. Two unrelated reverse-engineering efforts (a community project with no access to Cync's
APK, and this session's direct decompile) converging on identical byte values is strong evidence
this is standard Telink Mesh SDK behavior, not something Cync-specific or a decompile artifact.
[`vpaeder/telinkpp`](https://github.com/vpaeder/telinkpp) is a second implementation validating the
same scheme. [`google/python-laurel`](https://github.com/google/python-laurel) is cited by an
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

Confirmed, `TelinkDeviceBleManager.m14334v` (~lines 1773-1811) and the pairing-response callback
`C2184d.mo14353a` (~lines 99-126):

1. Phone writes to the **pairing characteristic** (`...1914`): `[0x0C] + random[0:8] +
   AES_ECB(key=fixedDefaultKey, data=XOR(pad16(meshName), pad16(meshPass)))[0:8]` - 17 bytes.
   `fixedDefaultKey = {0xA0,0xA1,...,0xA7, 0x00×8}` (a well-known Telink SDK default pairing key,
   not per-device or server-issued).
2. Device responds with its own random bytes + a matching proof; the app verifies
   `devRandom[0:8] ‖ AES(key=pad0(devRandom[0:8],16), data=XORnamepass)[0:8] == devRandom`
   (mutual authentication).
3. Both sides derive **`sessionKey = AES_ECB(key=XOR(meshName, meshPass), data=fixedDefaultKey[0:8]
   ‖ devRandom[0:8])`**.
4. The Telink AES primitive itself (`Telink.m13404b`, ~lines 131-155) has a documented SDK quirk:
   it reverses both the key and data bytes before the AES call, then reverses the result back - this
   is expected Telink behavior to replicate exactly, not a decompiler artifact to "fix".

**For a brand-new mesh**, `pairMesh$2.java` (~lines 195-266) reuses `getSessionKey()`'s freshly
derived key to AES-encrypt three more 17-byte packets (mesh name / password / LTK, prefixed
`0x04`/`0x05`/`0x06` respectively) written to the same pairing characteristic - handing the device
its permanent mesh credentials, encrypted with the just-negotiated session key. No network call
anywhere in this sequence - see [cloud_independence_research.md](cloud_independence_research.md)
for the corroborating higher-level finding (mesh credentials come from an existing hub over BLE or
are synthesized locally); this is the byte-level mechanism behind that conclusion.

## Command encryption (post-pairing)

Confirmed, `TelinkDeviceBleManager.m14326L` ("writeCommand", ~lines 1229-1428) and
`$writeCommand$2.java`:

- **Plaintext packet**: `[seq(2B, random)] + [opcode(1B)][0x00][0x00][dest_lo(1B)][dest_hi(1B)] +
  commandBody`. `dest` is the target `MeshAddress`, little-endian. The two zero bytes get
  overwritten with a 2-byte MIC (below).
- **IV**: `reverse(deviceMAC)[last 4 bytes] + 0x01 + packet[0:3]` (8 bytes total).
- **MIC**: `AES_ECB(sessionKey, IV ‖ length ‖ zero-padding)`, then a CBC-MAC-style fold over the
  payload; the first 2 result bytes overwrite `packet[3:5]`.
- **Payload encryption**: a CTR-like keystream - repeatedly AES-encrypting an incrementing nonce
  block (the IV with an incrementing counter byte per 16-byte block) and XORing the result into
  `packet[5:]`.
- The finished encrypted packet is written to the **command characteristic** (`...1912`) via a
  `WriteRequest` (`$writeCommand$2.java:205,218`), followed by a `SleepRequest` - **20ms for a group
  address, 320ms otherwise** (`$writeCommand$2.java:222`) - real inter-command throttling a working
  client would need to replicate to avoid overwhelming the device.

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
(`Utilities$chunkByteArray$1.java:32-34`). Each chunk becomes its own **independently
mesh-command-encrypted** write (using the command-encryption scheme above): `[0xF6, 0x11, 0x02,
0x02] + [chunkIndex] + ≤8 payload bytes`, with the chunk index reused as that packet's outer
mesh-header opcode byte (`SetWifiCommand.java:120-131`). In other words, WiFi credential chunking
happens *above* the encryption layer, as N separate encrypted mesh commands sent in sequence - not
one long GATT write.

## Confidence summary and open items

**High confidence, directly read from decompiled source, independently cross-validated on the
UUIDs**: service/characteristic UUIDs and roles, the pairing/session-key derivation algorithm shape,
the MIC+CTR-style command encryption shape, the notification type-byte scheme, the `SetWifiCommand`
chunk/payload layout.

**Lower confidence / genuinely open**:
- Exact semantic mapping of the `Telink.OPCODE` enum's ordinal values to the literal integer opcodes
  actually used in wire writes (code uses literals, not the enum, at the actual write sites).
- `f28869c`/`f28871e` (the byte-reversed service/characteristic pair) - purpose not determined.
- A few index-arithmetic details deep in the MIC-computation loop (e.g. `i2 = len - 5`-style offset
  math) - the algorithm *shape* is unambiguous, but exact byte offsets should be verified against a
  real packet capture before trusting a port to work against real hardware. This is exactly the kind
  of detail that's cheap to get wrong in a manual transcription and only surfaces empirically.

**Recommended next steps, in order of cost**: (1) clone and read `python-dimond`/`telinkpp`/
`python-laurel` directly - if `python-laurel`'s implementation matches what's documented here
byte-for-byte, most of the mesh-pairing risk is retired for free; (2) prototype the `SetWifiCommand`
chunking scheme specifically (the one piece with no prior art) against a real device, since that's
the part most likely to have a transcription error; (3) a live BLE capture during a real pairing
session remains the only way to get full certainty on the MIC-loop offset details, same as the
`docs/cloud_independence_research.md`'s original recommendation - now scoped much more narrowly.
