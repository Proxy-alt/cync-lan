"""Constants for the Cync LAN integration."""

DOMAIN = "cync_lan"

# ConfigEntry.data keys (set once, immutable after setup - credentials/identity)
CONF_ACCOUNT_USERNAME = "account_username"
CONF_ACCOUNT_PASSWORD = "account_password"

# ConfigEntry.options keys (user-changeable via the options/reconfigure flow)
CONF_LOCAL_PORT = "local_port"
CONF_EXPORT_REFRESH_INTERVAL = "export_refresh_interval"
CONF_ENABLE_LIGHT_GROUPS = "enable_light_groups"
CONF_HIDE_GROUP_MEMBERS = "hide_group_members"
# Opt-in gate for the experimental_* services. Every one of them sends a
# mesh command whose cmd_code is PREDICTED from a length formula rather than
# confirmed against a packet capture (see docs/mesh_opcodes.md), so they are
# off unless the user turns them on from the hub's Configure screen.
CONF_ENABLE_EXPERIMENTAL = "enable_experimental"
# Writes every unrecognised packet, as hex, to its own log file. Off by
# default because it is noisy; the one thing that identifies a non-Cync
# client connecting to the listener, which is otherwise invisible without
# packet capture on the host.
CONF_CAPTURE_UNKNOWN_PACKETS = "capture_unknown_packets"
# Switches the hub-family commands to the "bare" envelope - no 7-byte mesh
# routing block, length field 7 shorter. The decompiled phone app builds hub
# commands that way; whether our device-facing wire agrees is unproven, so
# this is one arm of an A/B test rather than a fix. Only offered once
# experimental commands are on, because the hub family is entirely
# experimental. See docs/hub_envelope_ab_test.md.
CONF_HUB_ENVELOPE_BARE = "hub_envelope_bare"

DEFAULT_LOCAL_PORT = 23779
DEFAULT_EXPORT_REFRESH_INTERVAL_HOURS = 24
DEFAULT_ENABLE_LIGHT_GROUPS = False
DEFAULT_HIDE_GROUP_MEMBERS = False
DEFAULT_ENABLE_EXPERIMENTAL = False
DEFAULT_CAPTURE_UNKNOWN_PACKETS = False
DEFAULT_HUB_ENVELOPE_BARE = False

MANUFACTURER = "Savant"

# Confirmed motion-sensor enums - see docs/mesh_opcodes.md
# (MotionSensorSensitivity.java). Shared by services.py's
# experimental_set_motion_sensor_settings and config_flow.py's options-flow
# wizard, which send the same command and must not drift apart.
MOTION_SENSOR_TYPE = {"motion": 1, "ambient_light": 2}
MOTION_SENSOR_SENSITIVITY = {"high": 0, "medium": 1, "low": 2}

# Diagnostic/noisy entities disabled by default (entity-disabled-by-default, gold)
DEFAULT_DISABLED_ENTITIES = {"app_mesh_active", "app_wifi_active", "mitm_mode"}

# Slot ordering matches sensor.py's _SLOT_LABELS and docs/cync_automations.md's
# cloud-JSON slot numbering.
SCHEDULE_SLOT_OPTIONS = {"morning": 0, "daytime": 1, "evening": 2, "sleep": 3}
# MotionSensorResponseMode.java ordinals - vacancy exists at the wire level but
# wasn't traced to a reachable UI path in the app.
SCHEDULE_MODE_OPTIONS = {"disabled": 0, "occupancy": 1, "vacancy": 2, "simple": 3}
# GroupReachFlag - ControlDeviceGroupCommand.java, see docs/mesh_opcodes.md's
# "Groups control" section.
REACH_FLAG_OPTIONS = {"normal": 0x00, "receive_only": 0x87}
# ScheduleFade.java's 1-byte signed enum - a coded duration bucket, not raw
# seconds. NO_FADE is (byte) -1, i.e. 0xFF.
FADE_OPTIONS = {
    "no_fade": 0xFF,
    "10_seconds": 1,
    "30_seconds": 2,
    "1_minute": 3,
    "5_minutes": 4,
    "10_minutes": 5,
    "20_minutes": 6,
    "30_minutes": 7,
}
