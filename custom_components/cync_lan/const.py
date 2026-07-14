"""Constants for the Cync LAN integration."""

from datetime import timedelta

DOMAIN = "cync_lan"

# ConfigEntry.data keys (set once, immutable after setup - credentials/identity)
CONF_ACCOUNT_USERNAME = "account_username"
CONF_ACCOUNT_PASSWORD = "account_password"
CONF_CORP_ID = "corp_id"
CONF_HOME_ID = "home_id"

# ConfigEntry.options keys (user-changeable via the options/reconfigure flow)
CONF_LOCAL_PORT = "local_port"
CONF_EXPORT_REFRESH_INTERVAL = "export_refresh_interval"
CONF_TCP_WHITELIST = "tcp_whitelist"

DEFAULT_LOCAL_PORT = 23779
DEFAULT_EXPORT_REFRESH_INTERVAL_HOURS = 24
EXPORT_REFRESH_INTERVAL = timedelta(hours=DEFAULT_EXPORT_REFRESH_INTERVAL_HOURS)

CYNC_API_BASE = "https://api.gelighting.com/v2/"
CYNC_CLOUD_IP = "34.73.130.191"
MANUFACTURER = "Savant"

# Diagnostic/noisy entities disabled by default (entity-disabled-by-default, gold)
DEFAULT_DISABLED_ENTITIES = {"app_mesh_active", "mitm_mode"}
