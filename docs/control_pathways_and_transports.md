# Control Pathways and Application Transport Protocols

This document details the alternative control pathways, transport mechanisms, and local communication channels supported across the Cync hardware ecosystem.

---

## 1. Complete App Transport Matrix (5 Protocols)

The decompiled Cync Android app (`cync_decompiled_v2`) implements 5 distinct transport channels:

```mermaid
graph TD
    App["Cync App / Control Client"]

    App --> T1["1. Local TCP Socket (Port 23778)"]
    App --> T2["2. Direct Local UDP (Port 5987)"]
    App --> T3["3. Direct BLE Mesh (Telink GATT)"]
    App --> T4["4. Cloud MQTT / TLS (AWS IoT)"]
    App --> T5["5. Tuya / Thingclips Cloud (Cameras)"]

    T1 --> D1["Wi-Fi Switches / Bulbs / Plugs"]
    T2 --> D2["Direct Connect Wi-Fi Devices"]
    T3 --> D3["Wire-Free BLE Switches & Sensors"]
    T4 --> D4["Cloud Push Sync & Status"]
    T5 --> D5["Indoor / Outdoor Cameras"]
```

### Transport Comparison Table

| Protocol Transport Channel | Port / Interface | Target Devices | HA Core Compatibility | IoT Class | Configuration Requirement |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1. Direct Local UDP** | UDP `5987` (`XlinkProperty.DEVICE_PORT`) | Direct Connect Wi-Fi Bulbs, Plugs, Switches | **Native / Core Ready** | `local_push` | Zero-config DHCP discovery (`88:50:F6` / `00:24:E4`) |
| **2. Local TCP Interception** | TCP `23778` (`cm.gecbyge.com`) | Wi-Fi Switches, Bulbs, Plugs | Advanced Option | `local_push` | DNS Redirection / Router Host Override |
| **3. Direct BLE Mesh** | Bluetooth GATT (`TLSR8258`) | C-Life/C-Sleep Bulbs, Wire-Free Switches | Separate Integration (`cync_ble`) | `local_push` | BlueZ / HA Bluetooth Adapter |
| **4. Cloud MQTT / AWS TLS** | TCP `8883` (`mqtt.gecbyge.com`) | All Account Devices | Cloud Standard | `cloud_push` | Cync Account Credentials |
| **5. Tuya / Thingclips** | HTTPS / P2P Stream | Indoor & Outdoor Cameras | Disqualified | `cloud_push` | Tuya P2P SDK |

---

## 2. Direct Local UDP Control (Port 5987)

### Mechanics
Direct Local UDP operates over port `5987` using `io.xlink.wifi.sdk.udp.UdpSendPacket` (`sendHandShake`, `sendPipe`, `sendSetDataPoint`, `sendPing`).

```mermaid
sequenceDiagram
    participant HA as Home Assistant (local_push)
    participant Device as Cync Direct Connect Device (IP: 192.168.1.100)
    
    HA->>Device: UDP Datagram to Port 5987 (Handshake Packet)
    Device-->>HA: Handshake Response ACK
    HA->>Device: UDP Datagram to Port 5987 (SetDataPoint / Pipe Packet)
    Device-->>HA: Command Execution ACK
```

### Advantages for Home Assistant Core Submission
1. **Zero DNS Redirection**: Does NOT require DNS server overrides (`cm.gecbyge.com`).
2. **100% UI Config Flow**: Onboards automatically via native Home Assistant DHCP discovery matching MAC prefixes `88:50:F6` and `00:24:E4`.
