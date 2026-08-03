# Decompiled App Feature Audit and Gap Analysis

This document summarizes the comprehensive audit of 1,054 Service classes and 288 Command classes in `cync_decompiled_v2`, highlighting implemented features and protocol roadmap gaps.

---

## 1. Decompiled App Feature Audit Matrix

| Feature Area | Decompiled Source Class | Status | Proposed Integration Path |
| :--- | :--- | :--- | :--- |
| **Hexagon Tile 2D Layout** | `TileServiceDefault.java` | Unimplemented in Core | Expose 2D coordinate grid & spatial array builder in `cync_lan`. |
| **Dynamic Light Show Engine** | `ShowServiceDefault.java` | Partially Implemented | Expose 5 light run modes (`Static`, `LightShow`, `MusicShow`, `Reveal`, `MultiColor`). |
| **Wireless Room Temperature Sensors** | `ThermostatServiceDefault.java` | Fully Supported | Expose sensor temperature readings and sensor priority weighting. |
| **Switch Indicator Ring LED** | `SwitchServiceDefault.java` | Fully Supported | Expose `select`, `number`, and `light` (RGB snapping) entities. |
| **Hardware Schedules / Routines** | `RoutineServiceDefault.java` | Fully Supported | Expose hardware schedule toggles and creation service. |
| **Wire-Free Motion Sensor Tuning** | `MotionServiceDefault.java` | Fully Supported | Expose sensitivity, timeout, and ambient light gate entities. |

---

## 2. Spatial 2D Layout Engine for Hexagon Tiles (`deviceType 140`)

### Decompiled Mechanics (`TileServiceDefault.java`)
- **Spatial Positioning**: Opcodes `0xF7 0x11 0x02 0x53` (`SetTileLayoutCommand`) send 2D Cartesian coordinates `(X, Y)` and rotation angles `0°-360°` for up to 24 connected tile panels.
- **Lighting Effects**: Opcodes `0x59` (`SetLightShowTileSpecificParameterCommand`) and `0x5A` (`SetMusicShowTileSpecificParameterCommand`) drive directional lighting waves (`Edge Sweep`, `Center Ripple`, `Cascade Wave`).
