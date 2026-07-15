"""Constants for the Cync LAN integration."""

import sys
from datetime import timedelta
from pathlib import Path

# Vendored copy of the `cync_lan` protocol package (devices.py, server.py,
# etc.) lives under vendor/cync_lan/, bundled directly in this integration
# rather than installed via manifest.json's `requirements`. That's a
# deliberate workaround, not a style choice: as of Home Assistant 2026.1,
# core's requirement parser started strictly validating against PEP 508 and
# now rejects git+https:// requirement URLs outright (see
# home-assistant/core#160450) - there is currently no way to depend on an
# unreleased-to-PyPI git package from a custom_component's manifest.json at
# all. Once cync-lan has a real PyPI release, this vendoring (and this
# sys.path insertion) can be removed in favor of a normal manifest.json
# requirement.
#
# This file is imported at module level by both __init__.py and
# config_flow.py, and does nothing else before this runs - that guarantees
# the vendored path is on sys.path before any `import cync_lan` anywhere in
# this integration, regardless of which of those two HA imports first.
_VENDOR_DIR = str(Path(__file__).parent / "vendor")
if _VENDOR_DIR not in sys.path:
    sys.path.insert(0, _VENDOR_DIR)

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
CONF_ENABLE_LIGHT_GROUPS = "enable_light_groups"

DEFAULT_LOCAL_PORT = 23779
DEFAULT_EXPORT_REFRESH_INTERVAL_HOURS = 24
EXPORT_REFRESH_INTERVAL = timedelta(hours=DEFAULT_EXPORT_REFRESH_INTERVAL_HOURS)
DEFAULT_ENABLE_LIGHT_GROUPS = False

CYNC_API_BASE = "https://api.gelighting.com/v2/"
CYNC_CLOUD_IP = "34.73.130.191"
MANUFACTURER = "Savant"

# Diagnostic/noisy entities disabled by default (entity-disabled-by-default, gold)
DEFAULT_DISABLED_ENTITIES = {"app_mesh_active", "app_wifi_active", "mitm_mode"}
