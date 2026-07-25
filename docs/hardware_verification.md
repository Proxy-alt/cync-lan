# Hardware verification status

Of the 27 experimental commands this project implements, **one** has been
confirmed against real hardware: `set_indicator_led`. The other 26 were built
from decompiled source with a `cmd_code` predicted by the length formula in
[mesh_opcodes.md](mesh_opcodes.md), and have never been observed working.

That is not 26 independent unknowns. Every command travels one of three
dispatch families, and a single successful test tells you about its whole
family. **Four tests would resolve the status of all 26.**

## The four questions

| # | Question | Cheapest test | Resolves |
|---|---|---|---|
| 1 | Is the `0x8E` mesh-relay envelope right? | **Identify** on any device | 12 commands |
| 2 | Is the hub-command envelope right? | **Delete scene**, or **Sync hub clock** | 11 commands |
| 3 | Do query replies come back over the TCP relay at all? | **Hub clock** sensor | 5 queries |
| 4 | Does the `0xE2` sub-command form work? | A light **transition** | 2 commands |

Question 2 deserves emphasis: **no** hub-family command has ever been
confirmed, and until cync-lan 0.2.0 all six of the original ones sent a length
field one byte short - so they were definitely broken, and are now only
*probably* right. If you test one thing beyond Identify, test this family.

Question 3 is the one that could invalidate a whole design direction. Every
query command depends on `_await_xlink_notification`, and there is no evidence
the reply channel rides the transport this project intercepts. If replies
never arrive, no further read-back commands are worth building.

## Family 1 — `0x8E` mesh-relay

One confirmed member, so the framing is probably right for all of them.

| Command | Sub | Needs | Status |
|---|---|---|---|
| `set_indicator_led` | `0x06` | any switch | **CONFIRMED** |
| `identify` | `0x03` | any device | untested |
| `set_dimmer_led_mode` | `0x62` | dimmer switch | untested |
| `set_dimmer_led_brightness` | `0x63` | dimmer switch | untested |
| `set_motion_sensor_settings` | `0x07` | motion sensor | untested |
| `set_motion_sensor_schedule` | `0x0B` | motion sensor | untested |
| `set_multicolor_gradient_mode` | `0x4E` | RGB strip | untested |
| `set_multicolor_segment_count` | `0x4E` | RGB strip | untested |
| `set_multicolor_segments` | `0x4E` | RGB strip | untested |
| `execute_scene` | - | a saved scene | untested |
| `add_to_scene` / `remove_from_scene` | - | a saved scene | untested |
| `set_group_membership` | - | a group | untested |

## Family 2 — hub commands

**Zero confirmed.** All six of the original members had a malformed length
field until cync-lan 0.2.0.

| Command | op | Needs | Note |
|---|---|---|---|
| `create_scene` / `delete_scene` | `0x10` / `0x1F` | a saved scene | length fixed in 0.2.0 |
| `create_schedule` / `delete_schedule` | `0x92` / `0x94` | a saved schedule | length fixed in 0.2.0 |
| `add_automation` / `toggle_automation` | `0x95` / `0x93` | a saved schedule | length fixed in 0.2.0 |
| `delete_automation` | `0x97` | a saved schedule | new in 0.3.0 |
| `delete_group` | `0x32` | a group | new in 0.3.0 |
| `set_group_power` | `0xD0` | a group | group-addressing unconfirmed |
| `set_time` | `0x40` | any hub | new in 0.4.0 |

## Family 3 — classic op families

| Command | op | Needs |
|---|---|---|
| `set_fine_brightness` | `0xE2/0x08` | dimmable light |
| `set_light_effect` / `set_lightshow` | `0xE2/0x07` | RGB light |

## Family 4 — query replies

All of these send fine; what is unproven is whether anything comes **back**.

`query_hub_info` (`0x4B`), `query_device_time` (`0x46`),
`query_sol_config` (`0xAD`), `query_hub_mesh_credentials` (`0x8A`),
plus `create_scene`/`create_schedule`, which read back an allocated id.

## Recording a result

A command that appears to do nothing is a useful result, not a failed test -
it says the predicted envelope for that family is wrong. When recording one,
note the device model, what you pressed, and what did or did not happen. Debug
logs help: set `custom_components.cync_lan` and `cync_lan` to debug, and the
outgoing packet hex is logged for every command.
