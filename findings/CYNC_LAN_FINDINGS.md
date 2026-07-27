# `sources/chip/` — Matter/CHIP Android SDK: identity, and is it actually used?

## 1. Confirmed identity

`sources/chip/` (171 files: `chip/clusterinfo`, `chip/devicecontroller`, `chip/devicecontroller/model`,
`chip/platform`, `chip/setuppayload`) is genuinely **Google's Matter (Project CHIP) Android SDK** —
not a coincidentally-named unrelated library. Evidence:

- `chip/devicecontroller/ChipDeviceController.java` — loads `System.loadLibrary("CHIPController")`
  (chip/devicecontroller/ChipDeviceController.java:60) and exposes the exact upstream CHIP Android
  API surface: `commissionDevice(...)`, `pairDevice(...)`, `openPairingWindow(...)`,
  `openPairingWindowWithPIN(...)`, `establishPaseConnection(...)`, `getCompressedFabricId(...)`,
  a `CompletionListener` with `onCommissioningComplete`/`onCommissioningStatusUpdate`/`onPairingComplete`,
  and `NOCChainIssuer` — all upstream `connectedhomeip` Android SDK class/method names.
- `chip/setuppayload/SetupPayloadParser.java` — loads `System.loadLibrary("SetupPayloadParser")`
  (line 39) and parses Matter QR codes / manual pairing codes (`fetchPayloadFromQrCode`,
  `fetchPayloadFromManualEntryCode`), matching the native `libSetupPayloadParser.so` bundled in
  the app's `resources/lib/` dirs mentioned in the task background.
- `chip/platform/AndroidChipPlatform.java` — `initChipStack()`, BLE/mDNS/key-value-store platform
  glue, again exact upstream CHIP Android platform shim naming.
- `chip/devicecontroller/ChipClusters.java`, `ClusterWriteMapping.java`, `ClusterReadMapping.java` —
  large auto-generated Matter cluster command/attribute bindings (the kind generated from the
  Matter XML cluster definitions upstream), consistent with genuine SDK code, not hand-rolled.

No explicit version string or copyright header survived JADX decompilation/R8 stripping (typical
for a vendored AAR build), so an exact CHIP SDK release/tag could not be pinned down from source
alone. Identity confidence: **high** — the native library names, method signatures, and generated
cluster-mapping code are unambiguous.

## 2. Is it actually used by Cync/Savant code? **No — it's dead weight for Cync's own product surface.**

The one live consumer of `chip.*` in the whole decompiled tree is **Tuya's bundled Matter SDK**,
under `com.thingclips.sdk.matter` / `com.thingclips.sdk.matterlib` (Tuya rebranded as "ThingClips").
That package genuinely builds and drives a `ChipDeviceController`:

- `com/thingclips/sdk/matterlib/pbddddb.java:116` and `:280` — `new ChipDeviceController(builderNewBuilder.build())`
- `com/thingclips/sdk/matterlib/pbddddb.java` imports `chip.devicecontroller.ChipDeviceController`,
  `ControllerParams`, `chip.platform.AndroidBleManager`, `AndroidChipPlatform`,
  `ChipMdnsCallbackImpl`, `DiagnosticDataProviderImpl`, `PreferencesConfigurationManager` — a
  complete, real commissioning-manager implementation.
- `com/thingclips/sdk/matter/presenter/ThingMatterController.java`,
  `ThingMatterDeviceConnectManager.java`, `ThingMatterMultipleFabricDevice.java`,
  `MatterNocChainIssuer.java`, `pipeline/MatterCommandUtils.java` — a full Tuya-side commissioning
  pipeline (NOC chain issuance, keypair delegates, QR/manual-code parsing via
  `chip.setuppayload.SetupPayloadParser`, NSD/mDNS discovery).

**But this Tuya Matter pipeline is never reached from Savant/Cync's own app code.** Searched
`sources/com/gelighting/` and `sources/com/savantsystems/` exhaustively:

- **Zero** Fragment/Activity/ViewModel in either package imports or references
  `ThingMatterController`, `ThingMatterDeviceConnectManager`, `ThingMatterMultipleFabricDevice`,
  `MatterNocChainIssuer`, or `MatterCommandUtils` (confirmed via `grep -rln` across both trees —
  no hits).
- Even *within* Tuya's own SDK, `ThingMatterController` is only referenced from other files inside
  `com/thingclips/sdk/matterlib/` itself (`qqdbbpp.java`, `pdbbqdp.java`, `qqpppdp.java`,
  `ddbdqbd.java`, `bdpdqbp.java`) — i.e. it isn't even wired to a UI layer within Tuya's bundled
  code as shipped in this app, let alone Cync's.
- No `new ChipDeviceController(...)` call exists anywhere outside `com/thingclips/sdk/matterlib/pbddddb.java`.
- No device-type/product enum entry for "Matter" exists in
  `com/gelighting/cbygekit/product/DeviceType.java` (searched for `MATTER`/`Matter` — no genuine
  hits; the only apparent match was JADX Kotlin-metadata byte noise, not real text).

### The handful of `chip.*` / `com.thingclips.sdk.matter*` imports found in Cync/Savant code are false leads — same pattern as the previously-found dead Tuya scene-rule engine

Grepping raw package-name strings across `com/gelighting/` and `com/savantsystems/` surfaces ~60
files importing something under `chip.devicecontroller`/`chip.platform`/`com.thingclips.sdk.matter*`.
Every single one checked resolves to one of two non-functional causes, confirming the precedent
noted in the task (a bundled Tuya scene-rule engine that turned out to be dead code, reached only
through a generic string-constant class):

1. **R8/D8 class-merging artifacts.** e.g. `chip.devicecontroller.C1448S0`
   (`sources/chip/devicecontroller/C1448S0.java`) is a synthetic lambda-holder class R8 merged
   into the `chip.devicecontroller` *package namespace* purely as a build optimization. Its actual
   interfaces are `ValueDescriptor`, `LibraryVersionComponent.VersionExtractor`,
   `TabLayoutMediator.TabConfigurationStrategy`, `ILogInterception` (Tuya logging),
   `MeshConnectStatusListener` (Tuya BLE mesh, unrelated to Matter), `ITemporaryCallBack`,
   `ObservableTransformer` — and only *incidentally* also
   `chip.clusterinfo.InteractionInfo.ClusterCommandFunction`. When Savant's
   `WalkthroughFragment.java:125` does `new C1448S0(19)`, it's instantiating the class purely for
   its `TabConfigurationStrategy` behavior (setting up a `TabLayoutMediator` for a tab UI) — the
   `chip.*` package name is a red herring caused by build-time merging, not a functional dependency
   on Matter.
2. **Borrowed numeric/string constants, never invoked as Matter APIs.** e.g.
   `com/gelighting/cbygekit/product/DeviceType.java:14532` calls
   `super(pbddddb.pppbppp, FirmwareVersionType.f38897d, 0, 4, null)` to construct a device-type
   entry (`SingleChipRevealFullColorHighLumenWafer4InchGen2`) — `pbddddb.pppbppp` is simply the
   `int` constant `180` (`com/thingclips/sdk/matterlib/pbddddb.java:35`), reused here as an
   unrelated chipset/firmware-revision ID with no semantic connection to Matter commissioning.
   Likewise `com/gelighting/cbygekit/services/devices/DeviceScanner.java:144` reads
   `qbbdpbq.pppbppp` — just the constant `6000` (a millisecond timeout,
   `com/thingclips/sdk/matterlib/qbbdpbq.java`), used purely as a magic number. Also
   `com/gelighting/cbygekit/services/show/C2504xb909bfd2.java:20` references
   `chip.platform.ConfigurationManager.kConfigKey_SetupDiscriminator` as a string constant, again
   in an otherwise Tuya/Firebase-adjacent utility class, not a commissioning call site.

### Corroborating evidence: a marketing string, not a feature flag

The only "Matter" string resource found anywhere under `resources/res/values*/strings.xml` is:

```
whats_new_updated_products_body = "Updated products with Matter compatibility are now on shelves!
Availability may vary by store, look for the Matter icon on the box."
```

This is a "What's New" marketing announcement telling users to buy *new hardware* with Matter
support and look for a physical box icon — it reads as Cync/GE announcing forthcoming/third-party
Matter-compatible products, not as evidence the app itself performs Matter commissioning today.

## 3. Commissioning flow trace

Not applicable — there is no live Cync/Savant-triggered commissioning flow to trace. The real
commissioning pipeline exists and is fully implemented in Tuya's bundled SDK
(`com.thingclips.sdk.matter*`, entry points `ThingMatterController` /
`ThingMatterDeviceConnectManager` / `pbddddb.java`'s `ChipDeviceController` construction), but it
has no caller anywhere in Savant's or Cync's own product code, and — as far as could be traced in
the time available — no caller even within Tuya's own bundled UI layer in this particular app
build. It appears to be a complete, unused dependency pulled in transitively as part of a
multi-brand Savant/Tuya shared build, exactly like the previously-identified dead Tuya scene-rule
engine.

## 4. Conclusion and confidence

**Dead weight for Cync's own purposes.** The Matter/CHIP Android SDK under `chip/` is real,
functional, genuine upstream SDK code, and it *is* driven by real code — but only by Tuya's
bundled `com.thingclips.sdk.matter*` package, which itself has zero reachable callers from
`com.gelighting.*` or `com.savantsystems.oneapp.*`. Every apparent cross-reference from Cync/Savant
code into the `chip.*` namespace resolves to either an R8 class-merging coincidence or a borrowed
numeric constant, never a functional/commissioning call.

**Confidence: high (not absolute).** This is based on exhaustive `grep`-based reference tracing
across the full decompiled tree plus manual inspection of every distinct call site found (not a
sample), so the negative claim ("zero real callers in Cync/Savant code") is well-supported. The
caveats: (a) decompiled/obfuscated Kotlin code can hide indirect invocation via reflection or
DI graphs (e.g. Dagger/Hilt) that plain-text grep wouldn't catch — `DaggerOneAppApplication_HiltComponents_SingletonC.java`
does reference `chip.devicecontroller.C1448S0`, but that's the same R8-merged synthetic class
described above, not a Hilt-provided `ChipDeviceController` binding; no Hilt `@Provides`/`@Module`
method returning `ChipDeviceController` or any `com.thingclips.sdk.matter*` type was found; (b) no
version string was recoverable, so it's possible (though unlikely given the code is fully wired
and native-lib-backed) that this is scaffolding for a not-yet-shipped feature rather than truly
inert legacy weight — either way, the practical answer for `cync-lan`'s provisioning research is
the same: this SDK provides no evidence of a real, currently-live Matter commissioning path for
Cync devices.
