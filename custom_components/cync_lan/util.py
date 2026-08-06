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


def _prepare_config_dir(config_dir: str) -> int:
    """Create the integration's config directory if needed and count the
    WiFi devices in whatever export already lives there.

    Both halves are blocking filesystem work, so they're deliberately one
    executor job rather than two: mkdir is cheap but still a syscall, and
    Home Assistant flags any blocking call made directly from the event
    loop (this module already carries scars from exactly that - see
    configure_environment's docstring).
    """
    os.makedirs(config_dir, exist_ok=True)
    return _count_wifi_devices(Path(config_dir) / "cync_mesh.yaml")


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


async def configure_environment(
    hass: HomeAssistant,
    username: str,
    password: str,
    capture_unknown_packets: bool = False,
    hub_envelope_bare: bool = False,
    capture_firmware: bool = False,
    cloud_passthrough: bool = False,
) -> None:
    """Point the upstream package's env-var-driven config at this entry.

    Must run before the first `import cync_lan.const` anywhere in the
    process - its module-level constants are read once, at import time.
    async_setup_entry and the config flow both call this before touching
    anything under the `cync_lan` package.

    Async because two of its steps require it: stable_secret() awaits
    Home Assistant's own persisted instance-UUID storage, and creating the
    config dir + sizing CYNC_MAX_TCP_CONN below touch the filesystem
    through the executor - confirmed via a real HA install flagging the
    earlier synchronous file read as a "Detected blocking call to
    open/read_text ... inside the event loop" warning.
    """
    config_dir = hass.config.path("cync_lan")
    wifi_device_count = await hass.async_add_executor_job(
        _prepare_config_dir, config_dir
    )
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
    # Read by cync_lan.const at import time, so a change only takes effect
    # after a full Home Assistant restart - a config-entry reload leaves the
    # already-imported module holding the old value. The options flow says so.
    os.environ["CYNC_UNSUPPORTED_RAW_DEBUG"] = "1" if capture_unknown_packets else "0"

    # Unlike everything else here, this one takes effect immediately.
    # cync_lan.devices re-reads CYNC_HUB_ENVELOPE on every hub command
    # rather than caching it at import (see its _hub_envelope_mode),
    # precisely so this can be flipped between the two candidate envelopes
    # without a Home Assistant restart - the A/B is only worth offering if
    # running both arms is cheap. See docs/hub_envelope_ab_test.md.
    os.environ["CYNC_HUB_ENVELOPE"] = "bare" if hub_envelope_bare else "routed"

    # Firmware capture. Like CYNC_HUB_ENVELOPE, this one is re-read where it
    # is used rather than cached at import, so toggling it takes effect on a
    # config-entry reload instead of needing a full Home Assistant restart -
    # see nCyncServer._start_firmware_watcher and sensor.py. The directory
    # lives under HA's own config dir so it survives updates and is reachable
    # over the Samba/SSH add-ons for pulling an image off the box.
    if capture_firmware:
        firmware_dir = os.path.join(config_dir, "firmware")
        await hass.async_add_executor_job(
            lambda: os.makedirs(firmware_dir, exist_ok=True)
        )
        os.environ["CYNC_FIRMWARE_CAPTURE_DIR"] = firmware_dir
    else:
        os.environ.pop("CYNC_FIRMWARE_CAPTURE_DIR", None)

    # Cloud passthrough. Re-read per accepted session rather than cached at
    # import (see the library's _cloud_passthrough_enabled), so this takes
    # effect on a config-entry reload - but only for sessions opened after
    # it, since a relay cannot join a stream whose handshake it missed. In
    # practice that means devices pick it up as they reconnect.
    os.environ["CYNC_CLOUD_PASSTHROUGH"] = "1" if cloud_passthrough else "0"

    os.environ["CYNC_MAX_TCP_CONN"] = str(
        max(wifi_device_count + _MAX_TCP_CONN_HEADROOM, _DEFAULT_MAX_TCP_CONN)
    )


def sleeping_battery_device(entry: Any, node: Any) -> bool:
    """Is this a battery device that is currently asleep?

    Battery-powered devices - motion sensors, wireless switches, remotes -
    sleep to save power and only join the mesh when woken by holding their
    off button for five seconds, until the LED turns green. Writes aimed at a
    sleeping device do not reach it.

    The check is deliberately the device's ordinary mesh online status, not
    anything sensor-specific, because that is exactly what the real Cync app
    checks: its wake-up screen just watches the same AvailabilityState flow
    every device type reports flip to Online, and no separate BLE
    "discoverable" state exists. See docs/mesh_opcodes.md's "Operational
    prerequisite" section for the decompiled-source trail.

    Worth gating on rather than sending blind: the app's own writeSettings /
    writeSchedule return a fake success when the target is offline, never
    transmitting. Sending anyway would reproduce that silent no-op, which is
    the single most confusing failure this integration can produce.
    """
    runtime_data = getattr(entry, "runtime_data", None)
    if runtime_data is None:
        return False
    return not bool(runtime_data.bridge.is_online(node.id))


def hub_envelope_supported() -> bool:
    """Does the installed cync_lan honour CYNC_HUB_ENVELOPE?

    The toggle writes an environment variable that older releases simply
    ignore. Silently ignoring it is the worst possible outcome here: the
    user would enable the alternate envelope, see hub commands behave
    exactly as before, and record that as "candidate B does not work" -
    a false negative in the one experiment the toggle exists to run.

    Import is lazy and inside the function because cync_lan.const reads
    its environment at import time (see configure_environment), so nothing
    here may import it at module scope.
    """
    try:
        from cync_lan import devices as _devices
    except Exception:  # noqa: BLE001 - absence is the thing being tested
        return False
    return hasattr(_devices, "_hub_envelope_mode")


def cloud_passthrough_supported() -> bool:
    """Does the installed cync_lan honour CYNC_CLOUD_PASSTHROUGH?

    Same hazard as hub_envelope_supported() and the same shape of answer:
    the option writes an environment variable that releases before 0.9.0
    simply ignore. Here the silent no-op is worse than a wrong experiment -
    someone turning this on expects their devices to reach the cloud again,
    and would have no way to tell "the relay is off" from "the relay is on
    and the cloud is refusing them".

    Lazy import for the reason given in configure_environment: cync_lan.const
    reads its environment at import time, so nothing may import it at module
    scope.
    """
    try:
        from cync_lan import devices as _devices
    except Exception:  # noqa: BLE001 - absence is the thing being tested
        return False
    return hasattr(_devices, "_cloud_passthrough_enabled")


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
