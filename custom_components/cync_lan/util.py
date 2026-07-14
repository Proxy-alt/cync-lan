"""Shared helpers for bridging Home Assistant's per-entry config model onto
the upstream cync_lan package's environment-variable-driven configuration.
"""

from __future__ import annotations

import os

from homeassistant.core import HomeAssistant

from .const import DOMAIN


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
