# `ge-sdk` (`com.gelighting.cbygekit`) — the part that speaks the protocol

3,630 classes. Everything cync-lan cares about is in here; the 14,000 classes
under `com/savantsystems/oneapp` are the UI that drives it.

Orientation only — for the extracted opcode table see
[`services/devices/command/COMMAND_CATALOGUE.md`](services/devices/command/COMMAND_CATALOGUE.md),
and for the tooling see [`../../../../tools/README.md`](../../../../tools/README.md).

Hand-written research that goes deeper than the generated catalogue — payload
layouts, chunking schemes, response correlation — lives in `tools/findings/`:

- `multipart_commands.md` — the `sendBlocks` chunking scheme, and the eleven
  commands that use it or their own variant
- `query_commands.md` — the shared query base, the request/notification
  envelopes, and the `0xEA` subtype dispatch table
- `hub_commands.md` — the WiFi-bridge family: consolidated opcode table,
  HDLC framing, and per-command payload layouts

## The shape of a command

A device command is one class in `services/devices/command/`, extending
`DeviceCommand<T>` (or `UnitDeviceCommand` when it returns nothing). Each one
declares its opcode as a static `byte[]` and implements the same two sends:

```java
mo14012f(TelinkCommandDelegate, MeshAddress, Continuation)   // BLE mesh
mo14013g(XlinkCommandDelegate,  MeshAddress, Continuation)   // Xlink / cloud
```

(Method names are R8-renamed and *renumber on every re-decompile* — match on
the parameter types, not the `mo14012f` spelling.)

Three things about that pair are easy to get wrong:

1. **The two paths can send different bytes.** `SetFanSpeedCommand` sends
   `F4 11 02 01` over BLE mesh and `E2 11 02 06` over Xlink.
2. **The Xlink path adds a one-byte outer opcode**, usually — but not always —
   the first byte of the inner array. `ExecuteSceneCommand` sends inner
   `EF 11 02` under outer `0x1E`, and carries an inline note saying so.
3. **Several classes build the payload in a helper** via
   `ByteArrayOutputStream`, so the opcode is not visible at the send site.
   A few branch between two arrays; those are genuinely ambiguous without
   reading the method.

## Where things are

| Package | What it holds |
| --- | --- |
| `services/devices/command` | 196 files, one per command. The opcodes. |
| `services/devices/controller` | Which transport a command goes out on. Five implementations: `BleDeviceController`, `BleProxyDeviceController`, `WifiProxyDeviceController`, `XlinkHubDeviceController`, `XlinkWifiDeviceController`, behind `DeviceController` / `DeviceControllerProvider`. |
| `services/devices/telink` | BLE mesh transport. `TelinkCommandDelegate` is the send interface; the `Telink*NotificationParser` classes decode what comes back, including the multipart reassembly for light-show and motion-sensor settings. |
| `services/devices/xlink` | Cloud/UDP transport. `XlinkCommandDelegate`. |
| `services/devices/datapoint` | The Xlink "datapoint" model — how state is reported back over the cloud path. |
| `services/devices/model` | `ConnectionType` (`ROUTING`, `BLE`, `BLE_PROXY`, `WIFI`, `WIFI_PROXY`), `DeviceModel`, `PowerState`, capability flags. |
| `product` | `MeshAddress` — address ranges, group-address offset, broadcast/self addresses — and the product tables. |
| `foundation/xlink` | `XlinkApiClient`, `XlinkEventManager`, and the push-notification types (`DataPointUpdateNotification`, `DeviceChangeNotification`, `PipeDataReceivedNotification`, …). |
| `foundation/wifi` | `HubManager`: hub scan, connect, WiFi credential provisioning, device access/subscribe keys. Also [`XLINKDTSL_FINDINGS.md`](foundation/wifi/XLINKDTSL_FINDINGS.md) — the DTLS layer is disabled in this build. |
| `foundation/model` | Shared enums and serialisable models. |
| `foundation/db` (`p014db`) | Room database. |
| `services/schedules` · `scenes` · `groups` · `motionSensor` · `show` · `multiColor` · `firmware` · `commission` | Feature services, each wrapping the commands above. |
| `debug/ui` (`p012ui`) | The app's own hidden debug screens — `CoreDebugDevicesFragment` and friends send raw commands, so they are a good place to see a command used in isolation. |
| `p013di` | Hilt modules. Ignore. |

## Reading it

```bash
tools/cyncdec.sh read SetBrightnessCommand      # hex bytes, names resolved
tools/cyncdec.sh names ConnectionType           # recovered constant names
tools/cyncdec.sh trace SetBrightnessCommand     # what it touches
tools/cyncdec.sh grep --scope com/gelighting/cbygekit/services/devices "0x73"
```

Some files carry inline `[cync-lan reverse-engineering note ...]` comments from
earlier analysis. Those are deliberate additions to decompiler output — trust
them over a fresh guess, and add to them rather than duplicating the work.
