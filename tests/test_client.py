"""Unit tests for the asynchronous Hoymiles Cloud API client."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import aiohttp

from custom_components.hoymiles_cloud.client import (
    HoymilesApiError,
    HoymilesAuthError,
    HoymilesCloudClient,
    HoymilesConnectionError,
    MicroInverterInfo,
    StationRealData,
    StationSummary,
)


@pytest.fixture
def client():
    """Create a client instance for testing."""
    return HoymilesCloudClient(username="test_user", password="secret_password")


@pytest.mark.asyncio
async def test_legacy_login_success(client):
    """Test legacy MD5 login flow."""
    with patch.object(
        client, "_async_v3_login", AsyncMock(return_value=(None, "v3 failed"))
    ), patch.object(
        client, "_async_legacy_login", AsyncMock(return_value=("test_legacy_token_123", None))
    ):
        token = await client.async_login()

    assert token == "test_legacy_token_123"
    assert client.token == "test_legacy_token_123"
    assert client.api_version == "0"


@pytest.mark.asyncio
async def test_argon_salted_login_success(client):
    """Test modern Argon2 salted challenge-response flow."""
    with patch(
        "custom_components.hoymiles_cloud.client.ARGON2_AVAILABLE", True
    ), patch.object(
        client, "_pre_inspect_v3", AsyncMock(return_value=("nonce123", "aabbcc", None))
    ), patch.object(
        client, "_async_argon_compute_challenge", AsyncMock(return_value="computed_challenge_hash")
    ), patch.object(
        client, "_login_v3_attempt", AsyncMock(return_value=("argon_token_xyz", None))
    ):
        token = await client.async_login()

    assert token == "argon_token_xyz"
    assert client.token == "argon_token_xyz"
    assert client.api_version == "1"


@pytest.mark.asyncio
async def test_v3_unsalted_login_success(client):
    """Test modern v3 unsalted flow when server returns empty salt."""
    with patch.object(
        client, "_pre_inspect_v3", AsyncMock(return_value=("nonce123", "", None))
    ), patch.object(
        client, "_login_v3_attempt", AsyncMock(return_value=("unsalted_token_456", None))
    ):
        token = await client.async_login()

    assert token == "unsalted_token_456"
    assert client.token == "unsalted_token_456"
    assert client.api_version == "1"


@pytest.mark.asyncio
async def test_auto_fallback_to_eu_gateway(client):
    """Test automatic fallback to EU gateway when Global fails."""
    async def mock_v3(url, *args, **kwargs):
        if "euapi" in url:
            return "eu_token_789", None
        return None, "Login failed on global"

    with patch.object(client, "_async_v3_login", side_effect=mock_v3), patch.object(
        client, "_async_legacy_login", AsyncMock(return_value=(None, "legacy failed"))
    ):
        token = await client.async_login()

    assert token == "eu_token_789"
    assert "euapi.hoymiles.com" in client.base_url


@pytest.mark.asyncio
async def test_home_profile_headers(client):
    """Test S-Miles Home mobile client headers."""
    from custom_components.hoymiles_cloud.const import PROFILE_HOME, PROFILE_WEB
    headers_home = client._get_headers(PROFILE_HOME)
    assert "sma/ad" in headers_home["User-Agent"]

    headers_web = client._get_headers(PROFILE_WEB)
    assert "Mozilla" in headers_web["User-Agent"]


@pytest.mark.asyncio
async def test_login_failure_raises_auth_error(client):
    """Test that failed authentication raises HoymilesAuthError."""
    with patch.object(
        client, "_async_v3_login", AsyncMock(return_value=(None, "User not found"))
    ), patch.object(
        client, "_async_legacy_login", AsyncMock(return_value=(None, "Legacy error"))
    ):
        with pytest.raises(HoymilesAuthError):
            await client.async_login()



@pytest.mark.asyncio
async def test_get_stations(client):
    """Test station listing."""
    client.token = "valid_token"
    mock_resp_data = {
        "status": "0",
        "data": {
            "list": [
                {
                    "id": 12345,
                    "station_name": "My Solar Roof",
                    "capacity": 2400.0,
                    "status": 1,
                    "timezone": "Europe/Helsinki",
                }
            ]
        },
    }

    with patch.object(client, "_request", return_value=mock_resp_data):
        stations = await client.async_get_stations()

    assert len(stations) == 1
    assert isinstance(stations[0], StationSummary)
    assert stations[0].id == 12345
    assert stations[0].name == "My Solar Roof"
    assert stations[0].capacity == 2400.0


@pytest.mark.asyncio
async def test_get_station_by_id(client):
    """Test fetching a specific station by ID."""
    client.token = "valid_token"
    mock_resp_data = {
        "status": "0",
        "data": {
            "id": 12345,
            "station_name": "My Solar Roof",
            "capacity": 2400.0,
            "status": 1,
            "timezone": "Europe/Helsinki",
        },
    }

    with patch.object(client, "_request", return_value=mock_resp_data):
        station = await client.async_get_station(12345)

    assert station is not None
    assert isinstance(station, StationSummary)
    assert station.id == 12345
    assert station.name == "My Solar Roof"



@pytest.mark.asyncio
async def test_get_real_data(client):
    """Test fetching real-time metrics for a station."""
    client.token = "valid_token"
    mock_resp_data = {
        "status": "0",
        "data": {
            "real_power": 1850.5,
            "today_eq": 8.42,
            "month_eq": 150.2,
            "total_eq": 3200.0,
            "co2_emission_reduction": 2.1,
            "plant_tree": 4,
            "data_time": "2026-10-08 12:00:00",
        },
    }

    with patch.object(client, "_request", return_value=mock_resp_data):
        real_data = await client.async_get_real_data(12345)

    assert isinstance(real_data, StationRealData)
    assert real_data.station_id == 12345
    assert real_data.real_power_w == 1850.5
    assert real_data.today_energy_kwh == 8.42
    assert real_data.total_energy_kwh == 3200.0
    assert real_data.co2_reduction_kg == 2.1


@pytest.mark.asyncio
async def test_get_devices(client):
    """Test parsing hardware tree into micro-inverters."""
    client.token = "valid_token"
    mock_tree = {
        "status": "0",
        "data": [
            {
                "id": 101,
                "text": "DTU-W100",
                "type": 1,
                "children": [
                    {
                        "id": 202,
                        "sn": "114172345678",
                        "model_no": "HM-800",
                        "soft_ver": "V01.00.12",
                        "type": 3,
                        "warn_data": {"connect": True, "warn": False},
                        "children": [],
                    }
                ],
            }
        ],
    }

    with patch.object(client, "_request", return_value=mock_tree):
        devices = await client.async_get_devices(12345)

    assert len(devices.micro_inverters) == 1
    micro = devices.micro_inverters[0]
    assert isinstance(micro, MicroInverterInfo)
    assert micro.id == 202
    assert micro.sn == "114172345678"
    assert micro.model == "HM-800"
    assert micro.connected is True
    assert micro.has_warning is False


@pytest.mark.asyncio
async def test_token_expiration_retry(client):
    """Test that status 100 triggers re-login and retries request."""
    client.token = "expired_token"
    client._token_acquired_at = 1000000000.0  # recently acquired so not proactive

    mock_resp_expired = AsyncMock()
    mock_resp_expired.status = 200
    mock_resp_expired.json = AsyncMock(
        return_value={"status": "100", "message": "Token expired"}
    )

    mock_resp_success = AsyncMock()
    mock_resp_success.status = 200
    mock_resp_success.json = AsyncMock(
        return_value={"status": "0", "data": {"real_power": 500}}
    )

    mock_session = MagicMock()
    mock_session.post.return_value.__aenter__.side_effect = [
        mock_resp_expired,
        mock_resp_success,
    ]
    mock_session.closed = False
    client._session = mock_session

    with patch.object(client, "async_login", AsyncMock(return_value="new_fresh_token")):
        res = await client._request("test/endpoint", {})

    assert res.get("status") == "0"
    assert res.get("data", {}).get("real_power") == 500


@pytest.mark.asyncio
async def test_token_expiration_retry_on_http_403(client):
    """Test that HTTP 403 Forbidden triggers re-login and retries request."""
    import time
    client.token = "expired_token"
    client._token_acquired_at = time.time()

    mock_resp_forbidden = AsyncMock()
    mock_resp_forbidden.status = 403

    mock_resp_success = AsyncMock()
    mock_resp_success.status = 200
    mock_resp_success.json = AsyncMock(
        return_value={"status": "0", "data": {"real_power": 300}}
    )

    mock_session = MagicMock()
    mock_session.post.return_value.__aenter__.side_effect = [
        mock_resp_forbidden,
        mock_resp_success,
    ]
    mock_session.closed = False
    client._session = mock_session

    with patch.object(client, "async_login", AsyncMock(return_value="fresh_token_403")):
        res = await client._request("test/endpoint", {})

    assert res.get("status") == "0"
    assert res.get("data", {}).get("real_power") == 300


@pytest.mark.asyncio
async def test_token_expiration_retry_on_user_not_login(client):
    """Test that status 1 with 'User not login' triggers re-login and retries request."""
    import time
    client.token = "expired_token"
    client._token_acquired_at = time.time()

    mock_resp_not_login = AsyncMock()
    mock_resp_not_login.status = 200
    mock_resp_not_login.json = AsyncMock(
        return_value={"status": "1", "message": "User not login"}
    )

    mock_resp_success = AsyncMock()
    mock_resp_success.status = 200
    mock_resp_success.json = AsyncMock(
        return_value={"status": "0", "data": {"real_power": 750}}
    )

    mock_session = MagicMock()
    mock_session.post.return_value.__aenter__.side_effect = [
        mock_resp_not_login,
        mock_resp_success,
    ]
    mock_session.closed = False
    client._session = mock_session

    with patch.object(client, "async_login", AsyncMock(return_value="fresh_token_login")):
        res = await client._request("test/endpoint", {})

    assert res.get("status") == "0"
    assert res.get("data", {}).get("real_power") == 750


@pytest.mark.asyncio
async def test_proactive_token_refresh(client):
    """Test that tokens older than 20 hours are refreshed proactively before request."""
    client.token = "old_token"
    client._token_acquired_at = 100.0  # Far in the past

    mock_resp_success = AsyncMock()
    mock_resp_success.status = 200
    mock_resp_success.json = AsyncMock(
        return_value={"status": "0", "data": {"ok": True}}
    )

    mock_session = MagicMock()
    mock_session.post.return_value.__aenter__.return_value = mock_resp_success
    mock_session.closed = False
    client._session = mock_session

    login_mock = AsyncMock(return_value="refreshed_proactive_token")
    with patch.object(client, "async_login", login_mock):
        await client._request("test/endpoint", {})

    login_mock.assert_called_once()


@pytest.mark.asyncio
async def test_get_real_data_null_data_payload(client):
    """Test handling of null data payload without raising AttributeError."""
    import time
    client.token = "valid_token"
    client._token_acquired_at = time.time()

    mock_tree = {
        "status": "0",
        "message": "success",
        "data": None,
    }

    with patch.object(client, "_request", return_value=mock_tree):
        real_data = await client.async_get_real_data(12345)

    assert isinstance(real_data, StationRealData)
    assert real_data.real_power_w == 0.0
    assert real_data.today_energy_kwh == 0.0


@pytest.mark.asyncio
async def test_transient_http_502_retry(client):
    """Test that a transient HTTP 502 Bad Gateway retries and succeeds."""
    import time
    client.token = "valid_token"
    client._token_acquired_at = time.time()

    mock_resp_502 = AsyncMock()
    mock_resp_502.status = 502

    mock_resp_success = AsyncMock()
    mock_resp_success.status = 200
    mock_resp_success.json = AsyncMock(
        return_value={"status": "0", "data": {"recovered": True}}
    )

    mock_session = MagicMock()
    mock_session.post.return_value.__aenter__.side_effect = [
        mock_resp_502,
        mock_resp_success,
    ]
    mock_session.closed = False
    client._session = mock_session

    with patch("asyncio.sleep", AsyncMock()):
        res = await client._request("test/endpoint", {})

    assert res.get("status") == "0"
    assert res.get("data", {}).get("recovered") is True


