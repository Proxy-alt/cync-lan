"""Tests for util.py, including inject-websession (platinum) verification."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.cync_lan.util import configure_environment, get_cloud_api


async def test_configure_environment_sets_expected_env_vars(hass, tmp_path):
    with patch.object(hass.config, "path", return_value=str(tmp_path / "cync_lan")):
        configure_environment(hass, "user@example.com", "hunter2")
    assert os.environ["CYNC_ACCOUNT_USERNAME"] == "user@example.com"
    assert os.environ["CYNC_ACCOUNT_PASSWORD"] == "hunter2"
    assert os.path.isdir(os.environ["CYNC_CONFIG_DIR"])


async def test_get_cloud_api_injects_has_shared_session(hass):
    """inject-websession (platinum): the session actually passed to
    CyncCloudAPI is Home Assistant's own shared aiohttp session, not one
    the API client creates for itself."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    from cync_lan.cloud_api import CyncCloudAPI

    expected_session = async_get_clientsession(hass)
    api = get_cloud_api(hass)

    assert api.http_session is expected_session
    assert api._session_injected is True

    # _check_session must be a no-op for an injected session - it should
    # not be replaced with a self-created one.
    await api._check_session()
    assert api.http_session is expected_session


async def test_cloud_api_close_does_not_close_injected_session(hass):
    """close() must never close a session this instance doesn't own -
    Home Assistant (or another integration) may still be using it."""
    from cync_lan.cloud_api import CyncCloudAPI

    mock_session = MagicMock()
    mock_session.closed = False
    mock_session.close = AsyncMock()
    api = CyncCloudAPI(session=mock_session)

    await api.close()

    mock_session.close.assert_not_called()


async def test_cloud_api_close_does_close_self_created_session():
    """Backward-compat check: when nothing was injected, close() keeps
    closing its own self-created session as before."""
    from cync_lan.cloud_api import CyncCloudAPI

    api = CyncCloudAPI()
    api._session_injected = False  # reset in case a prior test's singleton state leaked
    mock_session = MagicMock()
    mock_session.closed = False
    mock_session.close = AsyncMock()
    api.http_session = mock_session

    await api.close()

    mock_session.close.assert_awaited_once()
    assert api.http_session is None
