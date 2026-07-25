"""Fixtures for Cync LAN tests.

Requires the `pytest-homeassistant-custom-component` package, which is not
part of this repo's own runtime dependencies (it's a dev/test-only
dependency of the custom_component test suite, separate from the `cync-lan`
package's own pyproject.toml). Install it before running this suite:

    pip install pytest-homeassistant-custom-component

This has not been run against a live Home Assistant test environment as
part of this branch - see quality_scale.yaml for what's verified vs. what
still needs a real test run.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

# `pytest_plugins = "pytest_homeassistant_custom_component"` lives in the
# repo-root conftest.py - pytest hard-errors on that name in a non-top-level
# conftest, which made this whole suite uncollectable.


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Required for pytest-homeassistant-custom-component to load
    custom_components/ instead of only core integrations."""
    yield


@pytest.fixture
def mock_cloud_api():
    """Patch cync_lan.cloud_api.CyncCloudAPI for config flow tests, so no
    real network calls happen."""
    with patch("cync_lan.cloud_api.CyncCloudAPI") as mock_cls:
        instance = mock_cls.return_value
        instance._check_session = AsyncMock(return_value=None)
        instance.check_token = AsyncMock(return_value=False)
        instance.request_otp = AsyncMock(return_value=True)
        instance.send_otp = AsyncMock(return_value=True)
        instance.export_config_file = AsyncMock(return_value=True)
        yield instance


@pytest.fixture
def mock_parse_config():
    """One fake device so config-flow-test-coverage's happy path has a
    non-empty device_count to assert on."""
    fake_node = object()
    with patch(
        "cync_lan.utils.parse_config", new=AsyncMock(return_value={1: fake_node})
    ):
        yield {1: fake_node}
