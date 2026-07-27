# Native (.so) Library Inventory

Source: `resources/lib/{arm64-v8a,armeabi-v7a}/*.so` — both ABI dirs ship the **identical set of 55 libraries** (verified via `diff`), so each is listed once. All binaries are stripped ELF (no debug symbols); identification is from filename, embedded strings, `Java_*` JNI export symbols, and `NEEDED` link dependencies (via `objdump -p`).

Legend for "Referenced by app code": checked via `grep System.loadLibrary(...)` across `sources/`, plus JNI `Java_<package>_<Class>_<method>` export-name matching to actual Java/Kotlin classes in the tree.

## Potentially relevant to cync-lan's lighting/BLE/security work

| Library | Identified as | Version | Referenced by app code | Relevance note |
|---|---|---|---|---|
| `libSetupPayloadParser.so` | Matter/CHIP native setup-payload (QR/manual-code) parser | connectedhomeip (no version string embedded) | **Yes.** `System.loadLibrary("SetupPayloadParser")` (2 call sites); JNI exports `Java_chip_setuppayload_SetupPayloadParser_*` map directly to `chip/setuppayload/SetupPayloadParser.java` in the source tree | **Confirms the parallel Matter/CHIP investigation**: this .so is the native backend for the `chip.setuppayload` Java package. It *also* exports `Java_com_thingclips_sdk_matter_util_uuid_ThingMatterUUIDParser_parseUuidInfo`, i.e. the same binary backs both raw CHIP's `SetupPayloadParser` and ThingClips' (Tuya) `ThingMatterUUIDParser` wrapper — one native lib, two Java-side callers. Cross-referenced further: `com/gelighting/cbygekit/product/DeviceType.java` and several other `com/gelighting/*` files import `chip.platform.ConfigurationManager` and `com.thingclips.sdk.matterlib.*`/`matter.discover.*` directly, so Cync's own app code (not just the bundled Tuya SDK) does touch the Matter stack — likely for Matter-capable device onboarding. |
| `libCHIPController.so` | Matter/CHIP native device controller (commissioning, clusters, exchange/session layer) | connectedhomeip | **Yes.** `System.loadLibrary("CHIPController")` (3 call sites); ~2800 `Java_chip_devicecontroller_*` JNI exports match `chip/devicecontroller/*.java` | Companion binary to `libSetupPayloadParser.so` for the full CHIP controller stack (cluster read/write/subscribe, PASE/CASE commissioning). Same conclusion — genuinely wired in, presumably for Matter-device support alongside the legacy Cync/Xlink protocol. |
| `libBleLib.so` | ThingClips (Tuya) BLE mesh/DP framing + session-key JNI helper (not in the task's original flag list, but matches the "ble" keyword) | none embedded | **Yes.** `System.loadLibrary("BleLib")` in `com/thingclips/ble/jni/BLEJniLib.java`; JNI exports `madeSessionKey`, `parseKLVData`, `getCommandRequestData`, `parseDataRecived`, `crc4otaPackage`, `getNormalRequestData` match native method decls in `BLEJniLib.java` | This directly contradicts the assumption that Telink BLE mesh crypto is "pure Kotlin/Java" — `BLEJniLib` natively derives session keys and parses KLV-framed BLE packets for the **generic Tuya BLE SDK** (`com.thingclips.sdk.bluetooth.*` / `com.thingclips.sdk.ble.*`). However, this is Tuya's own generic BLE-provisioning protocol (DP/KLV framing, "thingble" tag), not the Telink mesh protocol cync-lan reimplements. Weak link found: `com/gelighting/cbygekit/product/DeviceType.java` imports several obfuscated `com.thingclips.sdk.bluetooth.*` classes, but only as constant fields (e.g. `pdqbbbp.qqpddqd`) passed to firmware-version-type constructors — looks incidental/vestigial, not an active call path into `BLEJniLib`. No Telink-specific mesh crypto found in native code; that logic still appears to live in Kotlin/Java per prior research. |
| `libmbedcrypto.so` | ARM Mbed TLS — crypto primitives module | mbed TLS (toolchain: Android clang 5.0.300080); no MBEDTLS_VERSION_STRING found in strings | **Indirect only.** No `System.loadLibrary("mbedcrypto")` in Java; it's a `NEEDED` link dependency of `libthing_security.so` | Not loaded directly from Java — it's pulled in transitively as `libthing_security.so`'s crypto backend (confirmed via `objdump -p libthing_security.so` NEEDED list: `libthing_security_algorithm.so`, `libmbedtls.so`, `libmbedx509.so`, `libmbedcrypto.so`). This is Tuya/ThingClips' generic **cloud/account security** layer, not BLE-mesh-adjacent — no evidence it touches the local Cync/Telink pairing path. |
| `libmbedtls.so` | ARM Mbed TLS — TLS 1.2/1.3 protocol module | same toolchain as above | Indirect (same as `libmbedcrypto.so`) | Same conclusion — part of ThingClips' `thing_security` bundle, likely used for Tuya's own cloud API TLS, not for Cync's local mesh/Xlink protocol. |
| `libmbedx509.so` | ARM Mbed TLS — X.509 cert parsing module | same toolchain as above | Indirect (same as `libmbedcrypto.so`) | Same bundle as above. |
| `libthing_security.so` | ThingClips (Tuya) security/crypto facade | Android clang 9.0.9 toolchain | **Yes.** `System.loadLibrary("thing_security")` present in sources | Links mbedtls trio + `libthing_security_algorithm.so`. Generic Tuya SDK security component (likely session/account crypto for the Tuya cloud integration, e.g. camera/IoT device linking) — no direct evidence of use in Cync's own BLE mesh pairing flow. |
| `libthing_security_algorithm.so` | ThingClips (Tuya) low-level crypto algorithms (AES/RSA helpers for `thing_security`) | Android clang 9.0.9 toolchain | **Yes.** `System.loadLibrary("thing_security_algorithm")` present in sources | Dependency of `libthing_security.so`; no exported `Java_*` JNI symbols of its own (called internally by `libthing_security.so`, not directly from Java). |
| `libthingmmkv.so` | Tencent MMKV (mobile key-value store), ThingClips-forked build ("SecurityFile") | Android clang 9.0.7/9.0.9 toolchain | **Yes.** `System.loadLibrary("thingmmkv")` (3 call sites); JNI exports `Java_com_thingclips_smart_android_SecurityFile_*` | Encrypted local key-value storage for the Tuya SDK — not crypto/protocol-relevant, just a settings/cache store. |
| `libThingP2PSDK.so` | ThingClips (Tuya) P2P camera SDK core (session/version negotiation, RTCP-ish framing) | exposes `ThingGetApiVersion()`, `getP2pVersion`/`getVersion` JNI methods | Not found via `System.loadLibrary` grep directly (likely loaded by a wrapper class not grepped by exact string) | Camera P2P transport, unrelated to lighting mesh. Listed here only because it was in the original flag list; confirmed camera-only. |
| `libxlinkdtsl.so` | **Xlink WiFi SDK** native crypto/session module (`io.xlink.wifi.sdk.util.XlinkDTSLUtils`) — not in the original flag list, but discovered while cross-checking | Android clang 11.0.5 toolchain | **Yes.** `System.loadLibrary("xlinkdtsl")`; JNI exports `Java_io_xlink_wifi_sdk_util_XlinkDTSLUtils_{encryptSendData,decryptReciveData,initDTSL,getState,clearPeer}` match `io/xlink/wifi/sdk/util/XlinkDTSLUtils.java` | **Worth flagging even though not on the original list**: Xlink is GE/Cync's own WiFi-hub protocol vendor (`io/xlink/wifi/sdk/*` — `XlinkAgent`, `XDevice`, `XlinkUdpService`, `XDeviceManage`), and `com/gelighting/cbygekit/foundation/wifi/XlinkAgentManager.java` + `XlinkHubDeviceController.java` call into it directly. This is the actual **native crypto layer for Cync's own WiFi/UDP local-network device protocol** (encrypt/decrypt send/receive data, per-device session state) — arguably more directly relevant to cync-lan's reverse-engineering goals than any of the Tuya/Matter libraries above, since it's Cync-branded, not third-party SDK boilerplate. |

## Camera/video/audio subsystem (unrelated to lighting mesh protocol)

| Library | Identified as | Version | Referenced | Note |
|---|---|---|---|---|
| `libavcodec.so` | FFmpeg codec lib | 4.2.3 (LGPL 2.1+) | indirect (linked by Thing* camera libs) | Video decode/encode |
| `libavfilter.so` | FFmpeg filter graph lib | 4.2.3 | indirect | Video filters |
| `libavformat.so` | FFmpeg container/demux lib | 4.2.3 | indirect | Container parsing |
| `libavutil.so` | FFmpeg utility lib | 4.2.3 | indirect | Shared FFmpeg utils |
| `libswresample.so` | FFmpeg audio resampler | 4.2.3-family | indirect | Audio resampling |
| `libswscale.so` | FFmpeg video scaler | 4.2.3-family | indirect | Video scaling |
| `libFFMuxing.so` | Thing camera SDK muxing wrapper | — | indirect | Records/muxes camera stream |
| `libFFmpegUtil.so` | Thing camera SDK FFmpeg helper | — | indirect | FFmpeg glue |
| `libThingCameraSDK.so` | Tuya camera SDK core (links FFmpeg 58/56 ABI) | — | `System.loadLibrary` likely via wrapper class | Camera streaming |
| `libThingFFmpegWrapper.so` | Tuya FFmpeg JNI wrapper | — | indirect | Camera streaming |
| `libThingMediaPlayerSDK.so` | Tuya media player | — | indirect | Camera playback |
| `libThingVideoCodecSDK.so` | Tuya video codec wrapper | — | indirect | Camera codecs |
| `libThingAudioEngineSDK.so` | Tuya audio engine (WebRTC-based AEC) | — | indirect | Camera 2-way audio |
| `libThingAvLogSDK.so` | Tuya AV logging/telemetry | — | indirect | Camera diagnostics |
| `libijkffmpeg.so` | Bilibili ijkplayer's bundled FFmpeg fork | ff3.3--ijk0.8.0 | `System.loadLibrary` (implied by ijkplayer) | Video playback |
| `libijkplayer.so` | Bilibili ijkplayer core | — | likely yes | Video playback |
| `libijksdl.so` | ijkplayer SDL rendering layer | — | indirect | Video rendering |
| `libh265decoder.so` | H.265/HEVC software decoder | — | indirect | Camera video decode |
| `libopenh264.so` | Cisco OpenH264 codec | — | indirect | Camera H.264 codec |
| `libVoAACEncoder.so` | Visual On AAC encoder | — | indirect | Camera audio encode |
| `libfaad.so` | FAAD2 AAC decoder (mislabeled strings show WebRTC APM, likely statically merged) | — | indirect | Camera audio decode |
| `libpcmjni.so` | PCM audio JNI bridge | — | indirect | Camera audio |
| `libmi_aec.so` | Acoustic echo cancellation lib | — | indirect | Camera 2-way audio |
| `libaudioproc.so` | WebRTC audio processing | — | indirect | Camera audio |
| `libwebrtc_apm.so` | WebRTC audio processing module | — | indirect | Camera audio |
| `libwebrtc_audio_preprocessing.so` | WebRTC audio preprocessing | — | `System.loadLibrary("webrtc_filter")` (name doesn't literally match but is the closest candidate) | Camera audio |
| `libPPPP_API.so` | Generic Chinese IP-camera "PPPP" P2P protocol SDK (CS2-family) | — | indirect | Camera P2P transport |
| `libYiDecrypt.so` | Xiaoyi (Yi) camera stream decryption | — | `System.loadLibrary("YiDecrypt")` | Camera decrypt |
| `libyimedia.so` | Xiaoyi (Yi) media/HLS-to-MP4 conversion | — | `System.loadLibrary("yimedia")` | Camera media |
| `libMotionMagnifier.so` | Motion-magnification video effect (Tuya feature) | — | indirect | Camera video effect |
| `libmp4recorder.so` | MP4 local recording | — | indirect | Camera recording |
| `libtrueimage-blender.so` | Image blending (HDR/panorama-style) utility | — | `System.loadLibrary("trueimage-blender")` | Camera image processing |
| `libimage_processing_util_jni.so` | AndroidX CameraX image processing JNI | — | indirect | Camera preview pipeline |
| `libimagepipeline.so` | Facebook Fresco image pipeline native lib | — | indirect | Image loading/caching |
| `libnative-imagetranscoder.so` | Facebook Fresco image transcoder (libjpeg-turbo 1.5.3) | 1.5.3 | indirect | Image resize/transcode |
| `libnative-filters.so` | Facebook Fresco native filters (blur, rounding) | — | indirect | Image processing |
| `libyuv.so` | Google libyuv (YUV colorspace conversion) | — | indirect | Video colorspace conversion |
| `libtensorflowlite.so` | TensorFlow Lite inference runtime | — | indirect | Likely on-device ML (motion/AI detection for camera) |
| `libnetwork-android.so` | Generic Android network JNI helper (unclear vendor) | — | `System.loadLibrary("network-android")` | Purpose unclear from strings; grouped here as no lighting/BLE hits |
| `libThingSmartLink.so` | Tuya SmartLink (WiFi SmartConfig/AP-mode pairing) | — | `System.loadLibrary("ThingSmartLink")` | Tuya's own WiFi onboarding — separate from Cync's Xlink WiFi pairing; not camera, but not lighting-mesh either; bundled Tuya SDK boilerplate |
| `libc++_shared.so` | LLVM libc++ shared runtime | — | `System.loadLibrary("c++_shared")` (5 call sites) | Generic C++ runtime dependency, shared by nearly every lib above |
| `libcrypto.1.1.so` | OpenSSL 1.1 libcrypto | 1.1.0 family | indirect | TLS backend, likely for FFmpeg/network stack (separate from mbedtls-based `thing_security`) |
| `libssl.1.1.so` | OpenSSL 1.1 libssl | 1.1.0 family | indirect | TLS backend, pairs with `libcrypto.1.1.so` |
| `liblog.so` | Android system logging stub (bundled copy) | — | indirect | Standard Android NDK lib, present in nearly every dependency list |

## Summary

- **55 distinct native libraries**, identical across `arm64-v8a` and `armeabi-v7a`.
- **11 libraries** flagged as potentially relevant to lighting/BLE/security work (10 from the original flag list + `libxlinkdtsl.so`, added because it's Cync's own WiFi-hub crypto SDK, discovered via `System.loadLibrary` cross-reference).
- **44 libraries** are camera/video/audio/imaging subsystem, unrelated to the Bluetooth-mesh lighting protocol.
- No native library name contains "mesh" or "telink" — confirms prior research that Telink BLE mesh session/packet crypto is implemented in Kotlin/Java, not native code, in this app version. `libBleLib.so` is a *different*, generic Tuya BLE-provisioning native helper (KLV/DP framing + session keys), not Telink-specific, and only weakly touched by Cync's own code (constant-field imports only).
