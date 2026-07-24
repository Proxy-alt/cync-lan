"""Shared helpers for bridging Home Assistant's per-entry config model onto
the upstream cync_lan package's environment-variable-driven configuration.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from homeassistant.core import HomeAssistant

from .const import DOMAIN

if TYPE_CHECKING:
    from cync_lan.cloud_api import CyncCloudAPI

_LOGGER = logging.getLogger(__name__)

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


async def configure_environment(hass: HomeAssistant, username: str, password: str) -> None:
    """Point the upstream package's env-var-driven config at this entry.

    Must run before the first `import cync_lan.const` anywhere in the
    process - its module-level constants are read once, at import time.
    async_setup_entry and the config flow both call this before touching
    anything under the `cync_lan` package.

    Async because two of its steps require it: stable_secret() awaits
    Home Assistant's own persisted instance-UUID storage, and sizing
    CYNC_MAX_TCP_CONN below reads cync_mesh.yaml off disk through the
    executor - confirmed via a real HA install flagging the earlier
    synchronous file read as a "Detected blocking call to open/read_text
    ... inside the event loop" warning.
    """
    config_dir = hass.config.path("cync_lan")
    os.makedirs(config_dir, exist_ok=True)
    os.environ["CYNC_ACCOUNT_USERNAME"] = username
    os.environ["CYNC_ACCOUNT_PASSWORD"] = password
    os.environ.setdefault("CYNC_CONFIG_DIR", config_dir)
    os.environ.setdefault("CYNC_SECRET_KEY", await stable_secret(hass))
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
    wifi_device_count = await hass.async_add_executor_job(
        _count_wifi_devices, Path(config_dir) / "cync_mesh.yaml"
    )
    os.environ["CYNC_MAX_TCP_CONN"] = str(
        max(wifi_device_count + _MAX_TCP_CONN_HEADROOM, _DEFAULT_MAX_TCP_CONN)
    )


def get_cloud_api(hass: HomeAssistant) -> "CyncCloudAPI":
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


async def refresh_cloud_export(hass: HomeAssistant) -> bool:
    """Best-effort: re-pull the Cync cloud export.

    export_config_file() assumes CyncCloudAPI.token_cache is already
    populated - that's only ever set as a side effect of check_token()
    (or the interactive OTP auth flow, which ends by writing it directly).
    Any caller outside the initial account-setup flow that skips
    check_token() first hits AttributeError: 'CyncCloudAPI' object has no
    attribute 'token_cache' - confirmed via a real user triggering it from
    the options flow's light-groups refresh; the periodic refresh timer in
    __init__.py had the exact same gap, silently swallowed by its own
    broad except-and-log.

    Returns False (without raising) if there's no valid/refreshable cached
    token - an unattended caller has no way to complete an interactive OTP
    challenge, so recovering from that requires the user to go through the
    config flow's reauth step themselves.
    """
    api = get_cloud_api(hass)
    if not await api.check_token():
        _LOGGER.warning(
            "Cannot refresh Cync cloud export: no valid cached auth token. "
            "Reauthenticate via the integration's reauth flow to restore it."
        )
        return False
    return bool(await api.export_config_file())


def build_device_group_map(groups: dict[int, dict[str, Any]]) -> dict[int, list[int]]:
    """Invert group_id -> {"device_ids": [...]} into device_id -> [group_id, ...].

    One-to-many: a device can belong to more than one group - a subgroup
    and its parent group each carry an independent sensor_schedules list
    (confirmed, see docs/cync_automations.md's "isSubgroup" section), so a
    device inside a subgroup needs both group_ids resolved to find all of
    its schedule data.
    """
    device_to_groups: dict[int, list[int]] = {}
    for group_id, group in (groups or {}).items():
        for dev_id in group.get("device_ids", []):
            device_to_groups.setdefault(dev_id, []).append(group_id)
    return device_to_groups


def group_sensor_schedules_for_device(
    groups: dict[int, dict[str, Any]],
    device_group_map: dict[int, list[int]],
    device_id: int,
) -> list[dict[str, Any]]:
    """[{"group_id", "group_name", "sensor_schedules"}] for every group
    `device_id` belongs to that has at least one decoded motion-sensor
    schedule slot. [] if the device isn't in any group, or none of its
    groups have schedule data.
    """
    result = []
    for group_id in device_group_map.get(device_id, []):
        group = groups.get(group_id) or {}
        schedules = group.get("sensor_schedules") or {}
        if not schedules:
            continue
        result.append(
            {
                "group_id": group_id,
                "group_name": group.get("name") or f"Group {group_id}",
                "sensor_schedules": schedules,
            }
        )
    return result


async def stable_secret(hass: HomeAssistant) -> str:
    """Derive a stable local secret for the token-cache Fernet cipher.

    Not a network secret - only protects the on-disk cached cloud token from
    casual reading, same threat model as the upstream add-on's default.

    Must be stable across HA restarts, not just within one process - a
    prior version generated a fresh random value into hass.data (in-memory
    only) on first access each process, which happened to be internally
    consistent within a run but meant the Fernet-encrypted token cache
    written under one restart's secret could never be decrypted again after
    the next restart. Confirmed via a real user's log: "Failed to decrypt
    or parse token cache. It may be corrupt or the secret key changed" -
    every restart, every time, silently forcing a fallback to OTP
    re-auth or a failed export depending on the call site. Home Assistant's
    own persisted instance UUID (homeassistant.helpers.instance_id, backed
    by .storage/core.uuid) is stable across restarts by design and already
    used for comparable per-installation identifiers elsewhere in HA core.
    """
    from homeassistant.helpers import instance_id

    return await instance_id.async_get(hass)
