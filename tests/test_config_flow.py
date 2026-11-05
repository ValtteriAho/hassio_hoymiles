"""Unit tests for the Hoymiles Cloud config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from custom_components.hoymiles_cloud.client import (
    HoymilesAuthError,
    HoymilesConnectionError,
    StationSummary,
)
from custom_components.hoymiles_cloud.const import DOMAIN


@pytest.mark.asyncio
async def test_flow_user_success_single_station():
    """Test successful user config flow with 1 station auto-detected."""
    from custom_components.hoymiles_cloud.config_flow import HoymilesCloudConfigFlow

    flow = HoymilesCloudConfigFlow()
    flow.hass = AsyncMock()

    mock_station = StationSummary(id=777, name="Home Solar", capacity=1500.0)

    with patch(
        "custom_components.hoymiles_cloud.config_flow.async_get_clientsession"
    ), patch(
        "custom_components.hoymiles_cloud.client.HoymilesCloudClient.async_login",
        AsyncMock(return_value="token123"),
    ), patch(
        "custom_components.hoymiles_cloud.client.HoymilesCloudClient.async_get_stations",
        AsyncMock(return_value=[mock_station]),
    ), patch.object(
        flow, "async_set_unique_id", AsyncMock()
    ), patch.object(
        flow, "_abort_if_unique_id_configured"
    ):
        result = await flow.async_step_user(
            {"username": "user@example.com", "password": "mypassword"}
        )

    assert result["type"] == "create_entry"
    assert result["title"] == "Home Solar"
    assert result["data"]["plant_id"] == 777
    assert result["data"]["username"] == "user@example.com"


@pytest.mark.asyncio
async def test_flow_user_invalid_auth():
    """Test invalid credentials error in config flow."""
    from custom_components.hoymiles_cloud.config_flow import HoymilesCloudConfigFlow

    flow = HoymilesCloudConfigFlow()
    flow.hass = AsyncMock()

    with patch(
        "custom_components.hoymiles_cloud.config_flow.async_get_clientsession"
    ), patch(
        "custom_components.hoymiles_cloud.client.HoymilesCloudClient.async_login",
        AsyncMock(side_effect=HoymilesAuthError("Bad creds")),
    ):
        result = await flow.async_step_user(
            {"username": "user@example.com", "password": "wrongpassword"}
        )

    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_auth"}
    assert result["description_placeholders"] == {"error_detail": "Bad creds"}


@pytest.mark.asyncio
async def test_flow_user_connection_error():
    """Test connection error in config flow."""
    from custom_components.hoymiles_cloud.config_flow import HoymilesCloudConfigFlow

    flow = HoymilesCloudConfigFlow()
    flow.hass = AsyncMock()

    with patch(
        "custom_components.hoymiles_cloud.config_flow.async_get_clientsession"
    ), patch(
        "custom_components.hoymiles_cloud.client.HoymilesCloudClient.async_login",
        AsyncMock(side_effect=HoymilesConnectionError("Timeout")),
    ):
        result = await flow.async_step_user(
            {"username": "user@example.com", "password": "password"}
        )

    assert result["type"] == "form"
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.asyncio
async def test_reauth_flow_success():
    """Test successful re-authentication flow."""
    from custom_components.hoymiles_cloud.config_flow import HoymilesCloudConfigFlow
    from custom_components.hoymiles_cloud.const import (
        CONF_BASE_URL,
        CONF_PLANT_ID,
        CONF_PLANT_NAME,
        CONF_PROFILE,
        CONF_USE_ESTAR,
    )
    from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
    from unittest.mock import MagicMock

    flow = HoymilesCloudConfigFlow()
    flow.hass = AsyncMock()

    mock_entry = MagicMock()
    mock_entry.data = {
        CONF_USERNAME: "user@example.com",
        CONF_PASSWORD: "old_password",
        CONF_PLANT_ID: 777,
        CONF_PLANT_NAME: "Home Solar",
        CONF_USE_ESTAR: False,
        CONF_BASE_URL: "https://global.api.hoymiles.com",
        CONF_PROFILE: "auto",
    }

    with patch.object(flow, "_get_reauth_entry", return_value=mock_entry), patch(
        "custom_components.hoymiles_cloud.config_flow.async_get_clientsession"
    ), patch(
        "custom_components.hoymiles_cloud.client.HoymilesCloudClient.async_login",
        AsyncMock(return_value="new_token"),
    ), patch.object(
        flow,
        "async_update_reload_and_abort",
        return_value={"type": "abort", "reason": "reauth_successful"},
    ) as mock_abort:
        reauth_result = await flow.async_step_reauth({})
        assert reauth_result["type"] == "form"
        assert reauth_result["step_id"] == "reauth_confirm"

        confirm_result = await flow.async_step_reauth_confirm({CONF_PASSWORD: "new_password"})

        assert confirm_result["type"] == "abort"
        assert confirm_result["reason"] == "reauth_successful"
        mock_abort.assert_called_once()
        _, kwargs = mock_abort.call_args
        assert kwargs["data_updates"][CONF_PASSWORD] == "new_password"


@pytest.mark.asyncio
async def test_reauth_flow_invalid_auth():
    """Test invalid credentials during re-authentication."""
    from custom_components.hoymiles_cloud.config_flow import HoymilesCloudConfigFlow
    from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
    from unittest.mock import MagicMock

    flow = HoymilesCloudConfigFlow()
    flow.hass = AsyncMock()

    mock_entry = MagicMock()
    mock_entry.data = {
        CONF_USERNAME: "user@example.com",
        CONF_PASSWORD: "old_password",
    }

    with patch.object(flow, "_get_reauth_entry", return_value=mock_entry), patch(
        "custom_components.hoymiles_cloud.config_flow.async_get_clientsession"
    ), patch(
        "custom_components.hoymiles_cloud.client.HoymilesCloudClient.async_login",
        AsyncMock(side_effect=HoymilesAuthError("Password incorrect")),
    ):
        result = await flow.async_step_reauth_confirm({CONF_PASSWORD: "wrong_password"})

    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_auth"}
    assert result["description_placeholders"]["error_detail"] == "Password incorrect"


@pytest.mark.asyncio
async def test_options_flow():
    """Test options flow initialization and update."""
    from custom_components.hoymiles_cloud.config_flow import (
        HoymilesCloudConfigFlow,
        HoymilesCloudOptionsFlow,
    )
    from custom_components.hoymiles_cloud.const import CONF_SCAN_INTERVAL
    from unittest.mock import MagicMock

    mock_entry = MagicMock()
    mock_entry.options = {CONF_SCAN_INTERVAL: 480}
    mock_entry.data = {}

    options_flow = HoymilesCloudConfigFlow.async_get_options_flow(mock_entry)
    assert isinstance(options_flow, HoymilesCloudOptionsFlow)

    # Attach mock entry to options flow for testing
    options_flow._config_entry = mock_entry

    form_result = await options_flow.async_step_init()
    assert form_result["type"] == "form"
    assert form_result["step_id"] == "init"

    submit_result = await options_flow.async_step_init({CONF_SCAN_INTERVAL: 600})
    assert submit_result["type"] == "create_entry"
    assert submit_result["data"] == {CONF_SCAN_INTERVAL: 600}


