# `libxlinkdtsl.so` findings

Research pass investigating the native library flagged in `sources/NATIVE_LIBRARIES.md` line 21
as "Xlink WiFi SDK native crypto/session module" — a bonus discovery during the native-library
inventory, not on the original flag list, but Cync/GE-branded and wired directly into the same
`Xlink`/`XlinkAgentManager` subsystem this project's mesh-opcode research (`docs/mesh_opcodes.md`)
already covers extensively. This note resolves what it actually is and whether it matters to
`cync-lan`.

## 1. Java-side call surface

The library is loaded and its native methods declared entirely in
`sources/io/xlink/wifi/sdk/util/XlinkDTSLUtils.java` (package `io.xlink.wifi.sdk.util`, **not**
`com.gelighting.*` — the `System.loadLibrary` call lives one layer down in the vendored Xlink SDK
that `XlinkAgentManager`/`XlinkAgent` sit on top of):

```java
static {
    System.loadLibrary("xlinkdtsl");
}

public native void clearPeer(String str, int i);
public native void decryptReciveData(byte[] bArr, String str, int i);
public native void encryptSendData(byte[] bArr, String str, int i);
public native int  getState(String str, int i);
public native void initDTSL(byte[] bArr, byte[] bArr2, byte[] bArr3);
public native String test();
```

Six native methods total:

| Java method | Signature | Purpose (inferred) |
|---|---|---|
| `initDTSL` | `(byte[], byte[], byte[]) -> void` | One-time DTLS context init. Only call site (`XlinkAgent.initDTLS()`, `XlinkAgent.java:427-429`) passes **`null, null, null`** — no PSK identity/key/cert material is ever actually supplied by this app build. |
| `encryptSendData` | `(byte[] data, String ip, int port) -> void` | Hand a plaintext outbound UDP payload + peer address to native for DTLS encryption. |
| `decryptReciveData` | `(byte[] data, String ip, int port) -> void` | Hand a raw inbound UDP payload + peer address to native for DTLS decryption. |
| `clearPeer` | `(String ip, int port) -> void` | Tear down DTLS peer/session state for one address (called on device logout/disconnect — see `XlinkAgent.java:930,1248`, `XDevice.java:149`, `XDeviceManage.java:103`). |
| `getState` | `(String ip, int port) -> int` | Query DTLS session state for a peer (handshake in progress / connected / etc). |
| `test` | `() -> String` | Trivial JNI liveness/version probe, unused elsewhere. |

Results come back **asynchronously via native-to-Java callbacks**, not return values — the native
code calls back into plain (non-native) Java methods of the same class:
`onReceiveEncryptData(byte[], byte[], byte[])`, `onReceiveDecryptData(byte[], byte[], byte[])`,
`onEventNotify(int, byte[], byte[])`, `onReceiveNoSendData(byte[], byte[], byte[])` — all defined
in `XlinkDTSLUtils.java` and matched 1:1 by exported native symbols (see §2). This is a full
bidirectional JNI design: Java pushes plaintext/ciphertext in, native does the DTLS work on a
background thread, native calls back into Java with the result.

Only caller of the whole class: `sources/io/xlink/wifi/sdk/XlinkAgent.java`, `XDevice.java`, and
`XDeviceManage.java` (all in the vendored `io.xlink.wifi.sdk` package). `XlinkAgentManager.java`
never touches `XlinkDTSLUtils` directly — it only calls `XlinkAgent.setUseDTLS(false)` (see §4),
one layer above.

## 2. Exported symbols (native side)

`file` on `resources/lib/arm64-v8a/libxlinkdtsl.so`: ELF 64-bit LSB shared object, ARM aarch64,
stripped, BuildID `d90328b2e8d6e8bd`. `nm -D` (dynamic symbol table) lists 110 entries. The six JNI
entry points line up exactly with the Java `native` declarations in §1:

```
Java_io_xlink_wifi_sdk_util_XlinkDTSLUtils_clearPeer
Java_io_xlink_wifi_sdk_util_XlinkDTSLUtils_decryptReciveData
Java_io_xlink_wifi_sdk_util_XlinkDTSLUtils_encryptSendData
Java_io_xlink_wifi_sdk_util_XlinkDTSLUtils_getState
Java_io_xlink_wifi_sdk_util_XlinkDTSLUtils_initDTSL
Java_io_xlink_wifi_sdk_util_XlinkDTSLUtils_test
```

Plus the Java-callback counterparts as plain exported C symbols (called via `FindClass`/
`GetMethodID`/`CallVoidMethod` from native, not `Java_...`-prefixed since Java calls native, not
the reverse):

```
onEventNotify   onReceiveDecryptData   onReceiveEncryptData   onReceiveNoSendData
```

Everything else exported is **stock tinyDTLS + supporting crypto primitives**, not
Cync/GE-authored code:

```
SHA256_Init SHA256_Update SHA256_Final SHA256_Transform SHA256_Data SHA256_End   (public-domain SHA-256, sha2.c naming)
rijndaelKeySetupEnc rijndaelEncrypt rijndael_set_key_enc_only rijndael_encrypt    (AES/Rijndael reference implementation)
dtls_new_context dtls_free_context dtls_connect dtls_connect_peer dtls_close
dtls_handle_message dtls_handshake_new/free dtls_new_peer dtls_destroy_peer dtls_get_peer
dtls_encrypt dtls_decrypt dtls_mac dtls_prf dtls_p_hash dtls_psk_pre_master_secret
dtls_ccm_encrypt_message dtls_ccm_decrypt_message
dtls_hmac_new/init/update/finalize/free
dtls_session_init/equals dtls_clock_init/offset/ticks dtls_check_retransmit
dtls_security_new/free dtls_renegotiate dtls_send dtls_write dtls_package_name/version
ecc_ec_mult ecc_ecdsa_sign ecc_ecdsa_validate ecc_is_valid_key ecc_g_point_x/y
netq_head/next/insert_node/remove/pop_first/node_new/node_free/delete_all
crypto_init peer_init tinyDTLS_event logCallbackMethodHelper
```

This is, symbol-for-symbol, **Eclipse tinyDTLS** (the small-footprint DTLS 1.2 implementation
originally written for CoAP/IoT use, `dtls_*`/`netq_*`/`peer_init`/`crypto_init` naming is
tinyDTLS's own internal API, not something GE wrote from scratch) plus its bundled reference AES
(rijndael*) and SHA-256 implementations, plus a small ECC module for ECDSA. `tinyDTLS_event` and
`logCallbackMethodHelper` are the only symbols that look like app-specific glue (an event-dispatch
shim and a JNI logging bridge), consistent with GE/Cync having taken stock tinyDTLS source and
bolted a thin JNI wrapper onto it rather than writing DTLS themselves.

## 3. What operation it performs

**Strings** (`strings -n 6`) confirm the tinyDTLS identification and narrow the configured cipher
suite precisely:

- `"Android (6875598, based on r399163b) clang version 11.0.5 ..."` — NDK clang 11.0.5 toolchain (matches `NATIVE_LIBRARIES.md`'s note).
- `"NDK-DTLS"` — a build/package tag, likely the fork's project name (an NDK port of tinyDTLS).
- `"dtls_prepare_record(): encrypt using TLS_PSK_WITH_AES_128_CCM_8"` — the negotiated/only cipher suite: **PSK-authenticated key exchange, AES-128 in CCM mode with 8-byte auth tag** (RFC 6655), the standard tinyDTLS default suite for constrained IoT devices.
- `"get_psk_info()"`, `"no psk identity set in kx"`, `"no psk key for session available"`, `"default identity"`, `"secretPSK"` — the last two are tinyDTLS's own **stock example/test credentials** (`Client_identity` / `secretPSK` is the canonical tinyDTLS sample PSK pair from its upstream `tests/dtls-client.c`). Their presence here looks like leftover sample code rather than deployed secrets — see §4, since `initDTSL` is only ever invoked with `(null, null, null)`, meaning no real PSK identity/key is plumbed in from the Java side in this app build at all.
- ECC strings/exports (`ecc_ecdsa_sign`, `ecc_ecdsa_validate`, `ecc_g_point_x/y`) indicate the ECDHE-ECDSA cipher-suite code path also exists in the binary (tinyDTLS optionally supports `TLS_ECDHE_ECDSA_WITH_AES_128_CCM_8`), even though the only cipher-suite string actually hit in the log/error strings is the PSK one.
- No AES/SHA byte-pattern search was necessary — the algorithm names come through directly as exported symbol names and log strings, which is stronger evidence than a magic-constant grep would have been. (Skipped the S-box/constant byte search since the symbol table already gives 1:1 identification.)

**Data flow** (from the Java call sites in §1): `encryptSendData`/`decryptReciveData` take a raw
UDP payload byte array plus the peer's IP string and port, and hand it to tinyDTLS's per-peer
session state machine (`dtls_new_peer`/`dtls_get_peer` keyed by address+port). This is classic
"secure the UDP wire" behavior: DTLS session set up via `initDTSL`, per-packet
encrypt-before-send / decrypt-after-receive keyed by (peer IP, peer port), and `clearPeer` to tear
down a peer's session (e.g. on device removal/logout). It is not a one-shot token/hash/signature
generator — it's a full transport-security session layer for the app's own **UDP** channel.

## 4. Relevance to `cync-lan`

**Conclusion: not relevant to the TCP wire protocol `cync-lan` already implements, and very likely
inert in current app builds regardless.** Reasoning, from the same decompiled tree:

1. **`cync-lan` intercepts the TCP path, which this library never touches.** `XlinkAgentManager.m13599f()` (`XlinkAgentManager.java:291-297`) configures the connection cync-lan reimplements: `XlinkAgent.setCMServer("cm.gelighting.com", 23779)` + `XlinkAgent.setTcpType(4)` (`TCP_TYPE_SSL`, `XlinkProperty.java`: `TCP_SSL_PORT = 23779`). That TCP/SSL connection is handled by `sources/io/xlink/wifi/sdk/XlinkTcpService.java`, which uses plain `javax.net.ssl.SSLContext`/`SSLSocketFactory`/`SSLSocket` (`XlinkTcpService.java:43-45,212-219`) — **standard TLS, not tinyDTLS**. `libxlinkdtsl.so` is never referenced anywhere in `XlinkTcpService.java`. The mesh-command wire format `docs/mesh_opcodes.md` documents (`XlinkDeviceManager.CommandDelegate` → `Xlink.a()` → the 0xD7/etc. op-code envelope) rides over *this* TLS-wrapped TCP socket, so `cync-lan`'s existing implementation (which works against real hardware today with no DTLS handling at all) is correctly scoped — it needed to replicate the TLS-over-TCP relay, not a DTLS-over-UDP layer.

2. **The DTLS/UDP layer this library backs is explicitly disabled at app init.** `XlinkAgentManager.m13599f()` calls `m13598e().setUseDTLS(false)` (`XlinkAgentManager.java:297`) immediately after configuring the SSL TCP connection. `XlinkAgent.isUseDTLS` defaults to `false` (`XlinkAgent.java:52`) and this app build never flips it on. Every UDP send/receive path in the vendored SDK gates on this flag before routing through `XlinkDTSLUtils`: `XlinkAgent.logout()` (`XlinkAgent.java:442`), `XlinkUdpService.java:199`, `UdpPacketReader.java:66`, `XDevice.java:148`, `XDeviceManage.java:102` — all check `if (!XlinkAgent.getUseDTLS() || ...) { /* send/receive in the clear via PacketWriter/PacketDecoder directly */ } else { /* route through XlinkDTSLUtils encrypt/decrypt */ }`. With the flag hardwired off, none of this build's UDP traffic ever reaches the native DTLS code at runtime.
3. **Even the one UDP port that's always exempt from DTLS (`XlinkProperty.DEVICE_PORT = 5987`) is a separate, local-only discovery/pairing channel** (`UdpPacketReader.java:66`: `if (!XlinkAgent.getUseDTLS() || this.receivePacket.getPort() == XlinkProperty.DEVICE_PORT)`), distinct from the port-23779 TCP relay `cync-lan` speaks to devices/hubs over.
4. **`initDTSL` is called with `(null, null, null)`** (`XlinkAgent.java:428`) — no real PSK identity or key material is ever supplied from the Java side in this build, reinforcing that this is vestigial/legacy plumbing (likely inherited from an older Xlink hardware generation that did UDP+DTLS locally, before Cync's product line settled on the cloud-relay-over-TLS-TCP design that `cync-lan` already fully implements) rather than an active security layer whose behavior `cync-lan` would need to emulate or worry about being locked out by.

**Confidence:** High that the library is stock tinyDTLS (symbol-for-symbol match, cipher-suite
string match) bound to a small JNI shim around a per-peer UDP encrypt/decrypt session. High that
it's disabled/unused in this app build's default configuration (explicit `setUseDTLS(false)` call,
`null` PSK material). Moderate-high (not fully proven without a live-traffic capture from a real
Cync app session) on the historical-purpose hypothesis — it's the most likely explanation given
the code shape, but it's possible some other unexamined code path (a firmware-update mode, a
factory/QA build variant, an older device generation still relying on local UDP-DTLS discovery) re-enables it under conditions not found by grep. Nothing found suggests this ever applies to the TCP relay protocol `cync-lan` implements.

## Symbol reference (raw `nm -D` output, `resources/lib/arm64-v8a/libxlinkdtsl.so`)

```
Java_io_xlink_wifi_sdk_util_XlinkDTSLUtils_clearPeer
Java_io_xlink_wifi_sdk_util_XlinkDTSLUtils_decryptReciveData
Java_io_xlink_wifi_sdk_util_XlinkDTSLUtils_encryptSendData
Java_io_xlink_wifi_sdk_util_XlinkDTSLUtils_getState
Java_io_xlink_wifi_sdk_util_XlinkDTSLUtils_initDTSL
Java_io_xlink_wifi_sdk_util_XlinkDTSLUtils_test
SHA256_Data SHA256_End SHA256_Final SHA256_Init SHA256_Transform SHA256_Update
crypto_init
dtls_ccm_decrypt_message dtls_ccm_encrypt_message dtls_check_retransmit
dtls_clock_init dtls_clock_offset dtls_close dtls_connect dtls_connect_peer
dtls_decrypt dtls_destroy_peer dtls_encrypt dtls_free_context dtls_free_peer
dtls_get_peer dtls_handle_message dtls_handshake_free dtls_handshake_new
dtls_hmac_finalize dtls_hmac_free dtls_hmac_init dtls_hmac_new dtls_hmac_update
dtls_init dtls_mac dtls_new_context dtls_new_peer dtls_p_hash dtls_package_name
dtls_package_version dtls_prf dtls_psk_pre_master_secret dtls_renegotiate
dtls_security_free dtls_security_new dtls_send dtls_session_equals
dtls_session_init dtls_ticks dtls_write
ecc_ec_mult ecc_ecdsa_sign ecc_ecdsa_validate ecc_g_point_x ecc_g_point_y ecc_is_valid_key
logCallbackMethodHelper
netq_delete_all netq_head netq_insert_node netq_next netq_node_free netq_node_new netq_pop_first netq_remove
onEventNotify onReceiveDecryptData onReceiveEncryptData onReceiveNoSendData
peer_init
rijndaelEncrypt rijndaelKeySetupEnc rijndael_encrypt rijndael_set_key_enc_only
tinyDTLS_event
(data/bss: _obj, cb, dst, dtls_clock_offset, recaddr, sockfd, toAddr)
```

Key strings (`strings -n 6`):
```
Android (6875598, based on r399163b) clang version 11.0.5 (...)
NDK-DTLS
DTLS_EVENT_CONNECTED
DTLSv12: initialize HASH_SHA256
dtls_prepare_record(): encrypt using TLS_PSK_WITH_AES_128_CCM_8
dtls_prepare_record(): encrypt using unknown cipher
get_psk_info()
default identity
secretPSK
no psk identity set in kx
no psk key for session available
```
