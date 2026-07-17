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
   `R_dev`, the device's own random contribution (`dimond/__init__.py:129,133`). **Correction**: the
   original pass described an explicit mutual-authentication check (verifying a proof the device
   sends back) - the real implementation performs **no such verification at all**, it just trusts
   whatever the device returns and proceeds. Either the real app does check and `python-dimond`
   simply skips it (plausible - a minimal client doesn't need to replicate defensive checks the
   official app makes), or the decompile mis-read a different code path as verification; unresolved,
   but a from-scratch client doesn't need the check either way since `python-dimond` works without
   it.
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

**Highest confidence - validated against real, working, cloned code (`python-dimond`)**: service/
characteristic UUIDs, the full session-key handshake algorithm (with the two corrections above), the
exact 20-byte command packet layout including the previously-missing vendor-ID field, the MIC +
single-block-keystream encryption algorithm, and basic status-notification parsing
(`data[7]==0xdc`, `data[10:18]`). The vendor-ID discovery also retroactively confirms cync-lan's own
existing `0x11,0x02` payload-prefix convention is this exact same field, not a coincidence.

**High confidence, decompile-only (no independent library covers new-device provisioning)**: the
notification type-byte table, `pairMesh$2.java`'s mesh-credential-handoff step, the `SetWifiCommand`
chunk/payload layout, and the 20/320ms inter-command throttling - `python-dimond`/`python-laurel`
only ever join an *already-provisioned* mesh, so none of this could be cross-checked this pass.

**Lower confidence / genuinely open**:
- Exact semantic mapping of the `Telink.OPCODE` enum's ordinal values to the literal integer opcodes
  actually used in wire writes (code uses literals, not the enum, at the actual write sites).
- `f28869c`/`f28871e` (the byte-reversed service/characteristic pair) - purpose not determined.
- Whether the Cync app itself performs the mutual-auth verification step the original decompile pass
  described (`python-dimond` doesn't, and doesn't need to) - unresolved, but not blocking for a
  from-scratch client.
- The `SetWifiCommand` chunking scheme specifically remains the least-validated piece of this whole
  document - it's the one part with genuinely zero independent confirmation.

**Recommended next steps, in order of cost**: (1) clone `vpaeder/telinkpp` too, as a second
cross-check on the command-encryption algorithm now confirmed above; (2) prototype the
`SetWifiCommand` chunking scheme specifically against a real device - now the single most valuable
remaining unknown, since everything else in the mesh-communication path (as opposed to provisioning
specifically) is validated against working code; (3) a live BLE capture during a real pairing
session remains the way to get full certainty on the WiFi-handoff step and confirm the
mutual-auth-verification question, same as `docs/cloud_independence_research.md`'s original
recommendation - now scoped to just the provisioning-specific pieces rather than the whole protocol.
