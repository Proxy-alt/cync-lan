"""Shared helpers for bridging Home Assistant's per-entry config model onto
the upstream cync_lan package's environment-variable-driven configuration.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from homeassistant.core import HomeAssistant

from .const import DOMAIN

# cync_lan.const's CYNC_MAX_TCP_CONN defaults to 8 - fine for the standalone
# add-on's own tuning.max_clients default, but far too low for a real
# household: every WiFi-connected switch/bulb/plug holds its own persistent
# TCP connection, and confirmed real accounts (see the HA integration's own
# quality_scale/session notes) commonly have 40-50+ such devices. A hard cap
# of 8 there means most devices get rejected outright ("server max TCP
# connections reached") and legitimate reconnects get treated as attackers.
_DEFAULT_MAX_TCP_CONN = 8
_MAX_TCP_CONN_HEADROOM = 4


def _count_wifi_devices(cfg_file: Path) -> int:
    """Count devices with a wifi_mac in the exported cync_mesh.yaml.

    Deliberately a standalone, minimal YAML read rather than importing
    cync_lan.utils.parse_config - this must run before cync_lan is ever
    imported at all (see configure_environment's docstring), and parsing
    the full config here would need CyncDevice, which only exists after
    that same import.
    """
    if not cfg_file.exists():
        return 0
    try:
        raw = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - sizing a tuning value, must not block setup
        return 0
    main_key = "exported_homes" if "exported_homes" in raw else "account data"
    count = 0
    for home in raw.get(main_key, {}).values():
        for device in home.get("devices", {}).values():
            if device.get("wifi_mac"):
                count += 1
    return count


def configure_environment(hass: HomeAssistant, username: str, password: str) -> None:
    """Point the upstream package's env-var-driven config at this entry.

    Must run before the first `import cync_lan.const` anywhere in the
    process - its module-level constants are read once, at import time.
    async_setup_entry and the config flow both call this before touching
    anything under the `cync_lan` package.
    """
    config_dir = hass.config.path("cync_lan")
    os.makedirs(config_dir, exist_ok=True)
    os.environ["CYNC_ACCOUNT_USERNAME"] = username
    os.environ["CYNC_ACCOUNT_PASSWORD"] = password
    os.environ.setdefault("CYNC_CONFIG_DIR", config_dir)
    os.environ.setdefault("CYNC_SECRET_KEY", stable_secret(hass))
    # cync_lan.const's CYNC_BASE_DIR defaults to "/root/cync-lan" - a path
    # that only exists in the standalone add-on's own Docker image (where
    # its Dockerfile also pre-generates the self-signed TLS cert under
    # {CYNC_BASE_DIR}/certs/). Neither the directory nor the cert exist in a
    # HA container, which crashed server.start() with FileNotFoundError on
    # load_cert_chain before this was set - confirmed via a real install.
    # Pointing CYNC_BASE_DIR here means the cert/key/static-file paths that
    # derive from it land inside HA's own writable config dir instead.
    os.environ.setdefault("CYNC_BASE_DIR", config_dir)

    # Sized from the actual exported device count rather than the package's
    # hardcoded default of 8 - see _DEFAULT_MAX_TCP_CONN's comment above.
    # No file yet (very first setup, before the initial export has run) or
    # a genuinely empty account both fall back to the original default
    # rather than 0, which would reject every connection outright. Not
    # setdefault: this is meant to be recomputed on every call (e.g. a
    # reload after the device count changed via a fresh export), though in
    # practice it only takes effect on setups where cync_lan.const gets
    # imported fresh (a full HA restart) - already-imported modules don't
    # re-read the environment on a config-entry reload within the same
    # running process, a limitation of the underlying env-var-at-import-
    # time design this integration has no control over.
    wifi_device_count = _count_wifi_devices(
        Path(config_dir) / "cync_mesh.yaml"
    )
    os.environ["CYNC_MAX_TCP_CONN"] = str(
        max(wifi_device_count + _MAX_TCP_CONN_HEADROOM, _DEFAULT_MAX_TCP_CONN)
    )


def get_cloud_api(hass: HomeAssistant):
    """inject-websession (platinum): construct CyncCloudAPI with Home
    Assistant's shared aiohttp session instead of letting it create (and
    leak, if never explicitly closed) its own. CyncCloudAPI is a singleton
    that only overwrites its session when one is explicitly passed (see its
    __init__ docstring), so every call site should go through this helper
    rather than constructing it bare.
    """
    from cync_lan.cloud_api import CyncCloudAPI
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    return CyncCloudAPI(session=async_get_clientsession(hass))


def stable_secret(hass: HomeAssistant) -> str:
    """Derive a stable local secret for the token-cache Fernet cipher.

    Not a network secret - only protects the on-disk cached cloud token from
    casual reading, same threat model as the upstream add-on's default.
    Cached on hass.data so it's generated once per HA process, not once per
    call (a fresh value every call would make the cached token unreadable
    the very next time it's read back).
    """
    key = f"{DOMAIN}_secret"
    if key not in hass.data:
        hass.data[key] = os.urandom(32).hex()
    return hass.data[key]
