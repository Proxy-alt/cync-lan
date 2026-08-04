# The Xlink local UDP path is real in the SDK and absent from the firmware

**Status: settled on hardware for provisioned devices, negative.** The vendor SDK bundled in the Cync
Android app defines a complete local UDP control protocol on port **5987** -
discovery, authenticated handshake, datapoint writes, raw pipe, keep-alive.
Every one of this account's **46 Wi-Fi devices refuses UDP 5987 outright**.
Nothing is bound to it. The path exists in the SDK and not in the product.

This corrects `cync-lan/docs/control_pathways_and_transports.md`, which listed
Direct Local UDP as a supported transport at "Native / Core Ready" confidence
with zero-config DHCP discovery, and named an `XlinkUdpClient` component that
was never written.

## Why this was worth testing

It would have been the best transport this project has. No DNS redirection, no
cloud, no Bluetooth, no connection slots, no adapter contention - a plain
unicast datagram to a device on the LAN. It would have made `cync-lan` core
eligible in a way DNS interception never will be, and it would have made
`cync-ble` unnecessary for every Wi-Fi device.

The auth material was already in hand, which is what made it look so close.
`cloud_api.py` already exports a per-home **`access_key`** alongside `id` and
`mac`, and the handshake wants exactly that value. No new credential, no new
cloud call, no capture. Only the transport was missing.

## The protocol, as the SDK defines it

Worth recording in full: the framing is the same one `cync-lan` already speaks
over TCP, so if this port were ever open the client would be a small piece of
work.

Header (`EncodeBuffer.setHeader` + `XTUtils.makeProtcol`), identical to the TCP
framing `PacketBuilder` already documents:

```
byte 0      (type << 4) | (response << 3) | version(3)
bytes 1-4   payload length, big endian
bytes 5+    payload
```

So `0x13` is type 1, request, version 3 - the same byte `PacketBuilder`
annotates as the app "login" family, from the same bit-packing.

Message types reachable over local UDP (`UdpSendPacket`, `PacketEncoder`):

| Type | Call | Purpose |
| :--- | :--- | :--- |
| 1 | `scanBuffer` | Broadcast discovery, by MAC or by product id |
| 2 | `handshakeBuffer` | Authenticate, receive a session id |
| 4 | `setDataPointLocalBuffer` | Write datapoints - the actual control op |
| 7 | `getDeviceSubKey` | Fetch subscribe key |
| 8 | `pipeLocalBuffer` | Raw pipe - carries the same payload `cync-lan` builds today |
| 9 | `setLocalPassWord` | Set local credential |
| 11 | `setLocalAccessKey` | Set access key |
| 13 | `pingLocalBuffer` | Keep-alive |
| 14 | `byebyeBuffer` | Tear down session |

Discovery payload (type 1), the one opcode that carries no authentication:

```
byte   3          protocol version literal
short  APP_PORT   the client's own bound port; the device answers there
byte   flags      0x11 = by MAC (bits 0 and 4), 0x02 = by product id
[short len]       MAC scans only, and only when device version >= 3
bytes  target     6 raw MAC bytes, or the product id as ASCII
```

Handshake (type 2) is the whole authentication story:

```
byte   version
bytes  MD5(access_key)   access_key as a 4-byte big-endian int, MD5'd
short  APP_PORT
byte   0
short  timeout
```

`XTUtils.MD5byte(int)` - a bare MD5 over the four key bytes. No nonce, no
challenge, no salt, so the handshake value is static per device and fully
derivable offline from a credential the cloud hands out. `ConnectDeviceTask2.
initStatus` bounds the key to `0 <= key <= 999999999`. The string-keyed variant
uses `hashAuth` instead, which is MD5 with every byte XOR'd by `53`.

After a successful handshake the device returns a session id, and every
subsequent op is scoped to it - types 4, 8, 13 and 14 all lead with the session
short rather than re-authenticating.

Note the correspondence that made this look most promising: type 8,
`pipeLocalBuffer`, is the local-UDP twin of the TCP pipe `cync-lan` already
parses (`DEVICE_REQUEST_X83`, `0x83` = type 8, version 3). The inner payload
would have been the command bytes `cync-lan` builds today. Only the outer
transport and the handshake were missing.

## Method

Two rounds, from the Home Assistant box, which sits on the device LAN.

1. **Broadcast.** SDK-shaped product-id scan to `255.255.255.255:5987` and
   `192.168.86.255:5987`, for both known product ids. Six-second listen.
   Zero replies - which alone proves nothing, since a broadcast that never
   leaves the interface looks identical to one nobody answers.

2. **Unicast, on connected sockets.** The 46 device IPs were taken from live
   TCP sessions on `23779` - every one of them was talking to `cync-lan` at
   that moment, so reachability was not in question. A *connected* UDP socket
   makes the kernel surface ICMP port-unreachable as `ECONNREFUSED`, which
   separates "no listener" from "listening but silent" from "filtered".

## Result

```
probed 46 hosts
replied : 0
refused : 46
silent  : 0
```

Unanimous ICMP port-unreachable. The devices' IP stacks answered - they are up,
reachable, and have nothing bound to 5987.

Because `DeviceAgent.jsonToDevice` reads a per-device `port` from cloud JSON
and only *falls back* to `XlinkProperty.DEVICE_PORT`, a shifted port was worth
excluding. Three devices were swept across `5980-6000`, `23770-23790`, and the
common suspects (`80`, `1900`, `5683`, `6666`, `8080`, `9999`):

```
probed 144 host/port pairs
replied : 0
refused : 144
silent  : 0
```

No UDP listener anywhere near the Xlink range. These devices appear to run no
UDP service at all.

## The app's own dispatch agrees

Stronger than the probe, because it is the shipped code deciding this at
runtime. `XlinkAgent.sendPipeData` is a router, and every command to a Wi-Fi
device passes through it:

```java
if (XlinkUdpService.isConnected() && device.isLanControlDev()) {
    return sendLocalPipeData(device, (byte) 0, bArr, i, sendPipeListener);
}
if (!XlinkTcpService.isConnected()) { return -4; }
if (device.isValidId()) {
    return TcpSendPacket.getInstance().sendPipe(device, (byte) 0, bArr, ...);
}
```

`isLanControlDev` is set only by `XDevice.setStates(0)`, which
`ConnectDeviceTask2` calls in exactly one place: the handshake success callback,
under the log line `"lan control device"`. The cloud branch sets `setStates(1)`
and logs `"cloud control device"`.

`ConnectDeviceTask2.run()` fires **both** paths concurrently - the local UDP
scan and the cloud probe - and whichever answers first decides the flag. So the
app attempts local UDP on every single device connect, and on this hardware the
local attempt can never win, because nothing answers. Every device is
permanently `cloud control`, and every command takes the TCP branch.

That is the branch `cync-lan` intercepts. It is not intercepting a fallback the
app rarely uses; it is intercepting the only branch that ever executes here.

## Scope of the negative

The 46 devices tested were all provisioned and in service - connected to
`cync-lan` over TCP at the moment of the probe. **This does not rule out the
port being open in a factory-fresh or setup state.**

There is a specific reason to suspect it might be. `HubManager.setWifiCredentials`
(`com.gelighting.cbygekit.foundation.wifi`) provisions a hub by calling
`scanHub` → `XlinkAgent.connectDevice` → `sendPipeData`, and `scanHub` is the
UDP broadcast discovery above. If that flow ever works over UDP rather than
falling through to cloud, the firmware must listen on 5987 at least during
commissioning.

That would still not yield a control transport - the window closes once the
device is provisioned, which is the state every device spends its life in - but
it would change "absent from the firmware" to "present only before
commissioning". Testing it needs a device held in setup mode, which was not
done.

## Why the SDK has it anyway

`io.xlink.wifi.sdk` is a general-purpose vendor IoT SDK, not Cync-specific
code, and Cync ships the whole thing. Its own defaults still point at the
vendor's infrastructure - `ADDRESS = "cm.ge.cn"`, `TCP_SSL_PORT = 23779` - and
that TCP half is unmistakably live: 46 devices are connected to `cync-lan` on
23779 right now, speaking the framing above. The local UDP half is the part of
the SDK the firmware never implemented. `UdpPacketWriter` is even marked
`@Deprecated` in the shipped code.

Worth keeping separate from the *other* Xlink in this codebase:
`packet/xlink_legacy.py` decodes the app's HDLC-style `0x7E`-delimited
notification frames from `XlinkTranslatorKt`. Same name, unrelated format.

## Consequence

The transport matrix loses a row. `cync-lan` reaches Wi-Fi devices by TCP
interception and nothing else, and the DNS-redirection requirement that keeps
it out of core stands unchallenged by this route.

The finding is not worthless. The handshake is now fully mapped and its
credential is already exported, so if a firmware version is ever found with the
port open, the client is a short piece of work. Anyone reading the SDK and
reaching the same hopeful conclusion can stop at this file.
