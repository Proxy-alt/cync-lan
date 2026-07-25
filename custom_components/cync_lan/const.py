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

DEFAULT_LOCAL_PORT = 23779
DEFAULT_EXPORT_REFRESH_INTERVAL_HOURS = 24
DEFAULT_ENABLE_LIGHT_GROUPS = False
DEFAULT_HIDE_GROUP_MEMBERS = False
DEFAULT_ENABLE_EXPERIMENTAL = False

MANUFACTURER = "Savant"

# Confirmed motion-sensor enums - see docs/mesh_opcodes.md
# (MotionSensorSensitivity.java). Shared by services.py's
# experimental_set_motion_sensor_settings and config_flow.py's options-flow
# wizard, which send the same command and must not drift apart.
MOTION_SENSOR_TYPE = {"motion": 1, "ambient_light": 2}
MOTION_SENSOR_SENSITIVITY = {"high": 0, "medium": 1, "low": 2}

# Diagnostic/noisy entities disabled by default (entity-disabled-by-default, gold)
DEFAULT_DISABLED_ENTITIES = {"app_mesh_active", "app_wifi_active", "mitm_mode"}
