"""Asynchronous Hoymiles S-Miles Cloud API client."""

from __future__ import annotations

import asyncio
import base64
import binascii
from dataclasses import dataclass, field
import hashlib
import json
import logging
import time
from typing import Any
from urllib.parse import urlparse

import aiohttp

from .const import (
    API_EU_BASE_URL,
    DEFAULT_BASE_URL,
    DEFAULT_HOME_APP_VERSION,
    DEFAULT_INSTALLER_APP_VERSION,
    ESTAR_BASE_URL,
    HOME_CLIENT_DC,
    HOME_CLIENT_TID,
    PROFILE_AUTO,
    PROFILE_HOME,
    PROFILE_INSTALLER,
    PROFILE_WEB,
)

try:
    from argon2.low_level import Type, hash_secret_raw

    ARGON2_AVAILABLE = True
except ImportError:
    Type = None
    hash_secret_raw = None
    ARGON2_AVAILABLE = False

_LOGGER = logging.getLogger(__name__)

# Endpoints
ARGON_PRE_INSP_API = "iam/pub/3/auth/pre-insp"
ARGON_LOGIN_API = "iam/pub/3/auth/login"
LEGACY_LOGIN_API = "iam/pub/0/auth/login"

ENDPOINT_STATION_FIND = {
    "0": "pvm/api/0/station/find",
    "1": "pvm/api/0/station/find",
}

ENDPOINT_STATION_PAGE = "pvm/api/0/station/select_by_page"

ENDPOINT_REAL_DATA = {
    "0": "pvm-data/api/0/station/data/count_station_real_data",
    "1": "pvm-data/api/0/station/data/count_station_real_data",
}

ENDPOINT_DEVICE_TREE = {
    "0": "pvm/api/0/station/select_device_of_tree",
    "1": "pvm/api/0/station/select_device_of_tree",
}

ENDPOINT_MICRO_DETAILS = {
    "0": "pvm/api/0/dev/micro/find",
    "1": "pvm/api/0/dev/micro/find",
}

HEADER_JSON = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,"
        " like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

TOKEN_MAX_AGE_SECONDS: float = 72000.0  # Proactively refresh token before 24h expiration (20 hours)



class HoymilesError(Exception):
    """Base exception for Hoymiles Cloud API."""


class HoymilesAuthError(HoymilesError):
    """Authentication or authorization failure."""


class HoymilesConnectionError(HoymilesError):
    """Connection error communicating with Hoymiles API."""


class HoymilesApiError(HoymilesError):
    """API responded with an unexpected error payload."""


@dataclass
class StationSummary:
    """Summary information for a Hoymiles solar station/plant."""

    id: int
    name: str
    capacity: float = 0.0
    status: int = 0
    timezone: str = ""
    address: str = ""


@dataclass
class StationRealData:
    """Real-time data for a solar station."""

    station_id: int
    real_power_w: float = 0.0
    today_energy_kwh: float = 0.0
    month_energy_kwh: float = 0.0
    total_energy_kwh: float = 0.0
    co2_reduction_kg: float = 0.0
    tree_planted: float = 0.0
    data_time: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class MicroInverterInfo:
    """Information and real-time state for a micro-inverter."""

    id: int
    sn: str
    model: str = ""
    firmware_version: str = ""
    connected: bool = True
    has_warning: bool = False
    alarm_code: int = 0
    alarm_message: str = ""
    ports: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PlantDevices:
    """Devices registered to a plant."""

    station_id: int
    micro_inverters: list[MicroInverterInfo] = field(default_factory=list)
    raw_tree: list[dict[str, Any]] = field(default_factory=list)


class HoymilesCloudClient:
    """Async client communicating with the Hoymiles S-Miles Cloud API."""

    def __init__(
        self,
        username: str,
        password: str,
        session: aiohttp.ClientSession | None = None,
        base_url: str = DEFAULT_BASE_URL,
        use_estar: bool = False,
        profile: str = PROFILE_AUTO,
    ) -> None:
        """Initialize the client."""
        self.username = username
        self.password = password
        self._custom_session = session
        self._session: aiohttp.ClientSession | None = session
        self.base_url = ESTAR_BASE_URL if use_estar else base_url
        self.estar_mode = use_estar
        self.profile = profile
        self.active_profile = profile
        self.token: str | None = None
        self._token_acquired_at: float = 0.0
        self.api_version: str = "1"
        self._lock = asyncio.Lock()

    def _get_headers(self, profile: str | None = None) -> dict[str, str]:
        """Build request headers for the specified or active client profile."""
        effective_profile = profile or self.active_profile or PROFILE_AUTO
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if effective_profile == PROFILE_HOME:
            headers["User-Agent"] = (
                f"sma/ad/{DEFAULT_HOME_APP_VERSION}/{HOME_CLIENT_TID}/{HOME_CLIENT_DC}"
            )
        elif effective_profile == PROFILE_INSTALLER:
            headers["User-Agent"] = f"S-Miles Installer/{DEFAULT_INSTALLER_APP_VERSION}"
            headers["X-Client-Type"] = "mobile"
            headers["App-Version"] = DEFAULT_INSTALLER_APP_VERSION
            headers["X-App-Version"] = DEFAULT_INSTALLER_APP_VERSION
        else:
            headers["User-Agent"] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,"
                " like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        return headers

    async def _get_session(self) -> aiohttp.ClientSession:
        """Ensure an open aiohttp ClientSession."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        """Close the underlying session if managed internally."""
        if self._custom_session is None and self._session and not self._session.closed:
            await self._session.close()

    @staticmethod
    def _decode_v3_salt(salt_value: str) -> bytes:
        """Decode a v3 salt value from hex or base64 format."""
        s = salt_value.strip()
        if len(s) % 2 == 0:
            try:
                return bytes.fromhex(s)
            except ValueError:
                pass

        try:
            return base64.b64decode(s, validate=True)
        except (binascii.Error, ValueError):
            return s.encode("utf-8")

    async def _async_argon_compute_challenge(self, salt_bytes: bytes) -> str:
        """Compute the Argon2 ID challenge hash offloaded from event loop."""
        if not ARGON2_AVAILABLE or hash_secret_raw is None or Type is None:
            raise RuntimeError("argon2-cffi is required for modern Hoymiles authentication")

        def _compute() -> str:
            raw = hash_secret_raw(
                secret=self.password.encode("utf-8"),
                salt=salt_bytes,
                time_cost=3,
                memory_cost=32768,
                parallelism=1,
                hash_len=32,
                type=Type.ID,
            )
            return raw.hex()

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _compute)

    async def _pre_inspect_v3(
        self, base_url: str, profile: str | None = None
    ) -> tuple[str | None, str | None, str | None]:
        """Run pre-inspect on the target base URL.

        Returns (nonce, salt, error_message).
        """
        url = base_url.rstrip("/") + "/" + ARGON_PRE_INSP_API
        session = await self._get_session()
        headers = self._get_headers(profile)

        try:
            async with session.post(
                url,
                json={"u": self.username},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return None, None, f"HTTP status {resp.status} on pre-inspect ({base_url})"
                try:
                    data = await resp.json()
                except Exception:
                    text = await resp.text()
                    return None, None, f"Non-JSON response from {base_url}: {text[:80]}"
        except Exception as err:
            return None, None, f"Network error contacting {base_url}: {err}"

        if str(data.get("status")) != "0":
            msg = data.get("message") or f"Pre-inspect returned status {data.get('status')}"
            return None, None, msg

        insp_data = data.get("data", {})
        nonce = insp_data.get("n")
        salt = insp_data.get("a")
        return nonce, salt, None

    async def _login_v3_attempt(
        self, base_url: str, credential_hash: str, nonce: str, profile: str | None = None
    ) -> tuple[str | None, str | None]:
        """Attempt a single v3 login request.

        Returns (token, error_message).
        """
        login_url = base_url.rstrip("/") + "/" + ARGON_LOGIN_API
        session = await self._get_session()
        login_payload = {"u": self.username, "ch": credential_hash, "n": nonce}
        headers = self._get_headers(profile)

        try:
            async with session.post(
                login_url,
                json=login_payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return None, f"HTTP status {resp.status} on login ({login_url})"
                try:
                    login_data = await resp.json()
                except Exception:
                    text = await resp.text()
                    return None, f"Non-JSON response from {base_url}: {text[:80]}"
        except Exception as err:
            return None, f"Network error contacting {login_url}: {err}"

        if str(login_data.get("status")) == "0":
            token = login_data.get("data", {}).get("token", "")
            if token:
                return token, None

        msg = login_data.get("message") or f"Login failed (status {login_data.get('status')})"
        return None, msg

    async def _async_v3_login(
        self, base_url: str, profile: str | None = None
    ) -> tuple[str | None, str | None]:
        """Execute modern v3 login supporting both salted and unsalted flows."""
        nonce, salt, err = await self._pre_inspect_v3(base_url, profile=profile)
        if not nonce:
            return None, err or "Failed to retrieve pre-inspection nonce"

        # Case 1: Server provided a salt -> Compute Argon2id hash
        salt_str = (salt or "").strip()
        if salt_str and ARGON2_AVAILABLE:
            try:
                salt_bytes = self._decode_v3_salt(salt_str)
                ch = await self._async_argon_compute_challenge(salt_bytes)
                token, login_err = await self._login_v3_attempt(
                    base_url, ch, nonce, profile=profile
                )
                if token:
                    return token, None
                _LOGGER.debug(
                    "Argon2 salted login failed on %s (profile %s): %s",
                    base_url,
                    profile,
                    login_err,
                )
            except Exception as ex:
                _LOGGER.debug("Argon2 challenge computation failed: %s", ex)

        # Case 2: Salt is empty/null or Argon2 not available -> Try unsalted browser formats
        md5_hex = hashlib.md5(self.password.encode("utf-8")).hexdigest()
        sha256_bytes = hashlib.sha256(self.password.encode("utf-8")).digest()
        sha256_hex = hashlib.sha256(self.password.encode("utf-8")).hexdigest()
        sha256_b64 = base64.b64encode(sha256_bytes).decode("ascii")
        composite_hash = f"{md5_hex}.{sha256_b64}"

        candidates = [
            ("composite", composite_hash),
            ("sha256_hex", sha256_hex),
            ("md5_hex", md5_hex),
        ]

        last_error = err or "Login failed"
        for i, (name, cand_hash) in enumerate(candidates):
            if i > 0:
                # Nonce is single-use: re-run pre-inspect to get a fresh nonce
                retry_nonce, _, retry_err = await self._pre_inspect_v3(
                    base_url, profile=profile
                )
                if not retry_nonce:
                    continue
                nonce = retry_nonce

            token, login_err = await self._login_v3_attempt(
                base_url, cand_hash, nonce, profile=profile
            )
            if token:
                _LOGGER.debug(
                    "v3 unsalted login succeeded on %s (profile %s) with variant %s",
                    base_url,
                    profile,
                    name,
                )
                return token, None
            last_error = login_err or last_error

        return None, last_error

    async def _async_legacy_login(
        self, base_url: str, profile: str | None = None
    ) -> tuple[str | None, str | None]:
        """Execute legacy MD5 authentication."""
        pass_hash = hashlib.md5(self.password.encode("utf-8")).hexdigest()
        url = base_url.rstrip("/") + "/" + LEGACY_LOGIN_API
        session = await self._get_session()
        headers = self._get_headers(profile)

        payload = {
            "password": pass_hash,
            "user_name": self.username,
        }
        try:
            async with session.post(
                url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    return None, f"HTTP status {resp.status}"
                data = await resp.json()
        except Exception as err:
            return None, str(err)

        if str(data.get("status")) != "0":
            return None, data.get("message") or "Legacy login failed"

        token_data = data.get("data", {})
        token = token_data.get("token") or token_data.get("estar_token")
        return token, None

    async def async_login(self) -> str:
        """Authenticate with the cloud and acquire an authorization token."""
        async with self._lock:
            # Determine profile sequence
            if self.profile == PROFILE_AUTO:
                profiles_to_try = [PROFILE_HOME, PROFILE_WEB, PROFILE_INSTALLER]
            else:
                profiles_to_try = [self.profile]

            # Determine URL gateways to try
            urls_to_try = [self.base_url]
            if not self.estar_mode:
                if self.base_url.rstrip("/") == DEFAULT_BASE_URL.rstrip("/"):
                    urls_to_try.append(API_EU_BASE_URL)
                elif self.base_url.rstrip("/") == API_EU_BASE_URL.rstrip("/"):
                    urls_to_try.append(DEFAULT_BASE_URL)

            failure_details: list[str] = []

            for prof in profiles_to_try:
                for target_url in urls_to_try:
                    _LOGGER.info(
                        "Attempting Hoymiles authentication against %s (profile: %s)",
                        target_url,
                        prof,
                    )

                    # 1. Try modern v3 login (salted Argon2 and unsalted candidates)
                    token, err = await self._async_v3_login(target_url, profile=prof)
                    if token:
                        self.token = token
                        self._token_acquired_at = time.time()
                        self.base_url = target_url
                        self.active_profile = prof
                        self.api_version = "1"
                        _LOGGER.info(
                            "Authenticated successfully on %s with profile '%s' (v3)",
                            target_url,
                            prof,
                        )
                        return token
                    if err:
                        msg = f"[{prof} @ {target_url}] v3: {err}"
                        _LOGGER.warning("Hoymiles auth attempt failed: %s", msg)
                        failure_details.append(msg)

                    # 2. Try legacy v0 MD5 login as fallback
                    token, err_leg = await self._async_legacy_login(
                        target_url, profile=prof
                    )
                    if token:
                        self.token = token
                        self._token_acquired_at = time.time()
                        self.base_url = target_url
                        self.active_profile = prof
                        self.api_version = "0"
                        _LOGGER.info(
                            "Authenticated successfully on %s with profile '%s' (legacy v0)",
                            target_url,
                            prof,
                        )
                        return token
                    if err_leg:
                        msg = f"[{prof} @ {target_url}] legacy: {err_leg}"
                        _LOGGER.warning("Hoymiles auth attempt failed: %s", msg)
                        failure_details.append(msg)

            # Choose the most specific error message to present to user
            best_error = "Invalid username or password"
            for detail in failure_details:
                lower = detail.lower()
                if (
                    "s-miles home" in lower
                    or "password" in lower
                    or "user" in lower
                    or "account" in lower
                ):
                    best_error = detail
                    break
            else:
                if failure_details:
                    best_error = failure_details[0]

            self.token = None
            self._token_acquired_at = 0.0
            _LOGGER.error(
                "All Hoymiles authentication attempts failed: %s", failure_details
            )
            raise HoymilesAuthError(best_error)

    async def _request(
        self,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        max_retries: int = 2,
    ) -> dict[str, Any]:
        """Make an authenticated POST request to the API with retries and auto-reauth."""
        now = time.time()
        if not self.token or (now - self._token_acquired_at > TOKEN_MAX_AGE_SECONDS):
            await self.async_login()

        url = self.base_url.rstrip("/") + "/" + endpoint.lstrip("/")
        session = await self._get_session()

        for attempt in range(max_retries + 1):
            headers = self._get_headers(self.active_profile)
            if self.token:
                headers["Authorization"] = self.token
                headers["token"] = self.token

            reauth_needed = False
            reauth_reason = ""
            data: Any = None

            try:
                async with session.post(
                    url,
                    json=payload or {},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status in (401, 403):
                        reauth_needed = True
                        reauth_reason = f"HTTP {resp.status}"
                    elif resp.status in (500, 502, 503, 504):
                        if attempt < max_retries:
                            _LOGGER.warning(
                                "Transient HTTP %s on %s, retrying in 2s (attempt %s/%s)...",
                                resp.status,
                                endpoint,
                                attempt + 1,
                                max_retries,
                            )
                            await asyncio.sleep(2)
                            continue
                        raise HoymilesConnectionError(
                            f"HTTP status {resp.status} on {endpoint}"
                        )
                    elif resp.status != 200:
                        raise HoymilesConnectionError(
                            f"HTTP status {resp.status} on {endpoint}"
                        )
                    else:
                        try:
                            data = await resp.json()
                        except Exception:
                            text = await resp.text()
                            if attempt < max_retries:
                                _LOGGER.warning(
                                    "Non-JSON response on %s, retrying in 2s (attempt %s/%s)...",
                                    endpoint,
                                    attempt + 1,
                                    max_retries,
                                )
                                await asyncio.sleep(2)
                                continue
                            raise HoymilesApiError(
                                f"Non-JSON response from {endpoint}: {text[:100]}"
                            )

                        status_str = str(data.get("status", "0"))
                        msg_lower = str(data.get("message", "")).lower()

                        auth_keywords = (
                            "token",
                            "login",
                            "not login",
                            "unauthorized",
                            "forbidden",
                            "timeout",
                            "session",
                            "expired",
                            "auth fail",
                        )
                        if status_str in ("100", "101", "102", "401", "403"):
                            reauth_needed = True
                            reauth_reason = f"status {status_str} ({data.get('message')})"
                        elif status_str != "0" and any(k in msg_lower for k in auth_keywords):
                            reauth_needed = True
                            reauth_reason = f"auth message: {data.get('message')}"

            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                if attempt < max_retries:
                    _LOGGER.warning(
                        "Network error on %s: %s, retrying in 2s (attempt %s/%s)...",
                        endpoint,
                        err,
                        attempt + 1,
                        max_retries,
                    )
                    await asyncio.sleep(2)
                    continue
                raise HoymilesConnectionError(
                    f"Connection error on {endpoint}: {err}"
                ) from err
            except HoymilesError:
                raise
            except Exception as err:
                raise HoymilesApiError(
                    f"Unexpected error on {endpoint}: {err}"
                ) from err

            if reauth_needed:
                _LOGGER.info(
                    "Token expired or rejected (%s), re-authenticating...",
                    reauth_reason,
                )
                self.token = None
                self._token_acquired_at = 0.0
                await self.async_login()

                headers = self._get_headers(self.active_profile)
                if self.token:
                    headers["Authorization"] = self.token
                    headers["token"] = self.token

                try:
                    async with session.post(
                        url,
                        json=payload or {},
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=20),
                    ) as retry_resp:
                        if retry_resp.status != 200:
                            raise HoymilesConnectionError(
                                f"HTTP status {retry_resp.status} on retry of {endpoint}"
                            )
                        data = await retry_resp.json()
                except aiohttp.ClientError as err:
                    raise HoymilesConnectionError(
                        f"Connection error on retry of {endpoint}: {err}"
                    ) from err

            if not isinstance(data, dict):
                raise HoymilesApiError(
                    f"Malformed response on {endpoint}: expected JSON object"
                )

            if str(data.get("status")) != "0":
                msg = data.get("message") or f"API error status {data.get('status')}"
                raise HoymilesApiError(f"Error on {endpoint}: {msg}")

            return data

        raise HoymilesConnectionError(f"Request failed after {max_retries} retries: {endpoint}")


    async def async_get_station(self, station_id: int) -> StationSummary | None:
        """Fetch details for a specific station ID using station/find."""
        endpoint = ENDPOINT_STATION_FIND.get(
            self.api_version, ENDPOINT_STATION_FIND["0"]
        )
        try:
            response = await self._request(endpoint, {"id": int(station_id)})
            data = response.get("data", {})
            if isinstance(data, dict) and "id" in data:
                return StationSummary(
                    id=int(data["id"]),
                    name=data.get("station_name") or data.get("name") or f"Plant {data['id']}",
                    capacity=float(data.get("capacity") or data.get("capacitor") or 0.0),
                    status=int(data.get("status", 0)),
                    timezone=str(data.get("timezone") or data.get("tz_name") or ""),
                    address=str(data.get("address") or data.get("location") or ""),
                )
        except Exception as err:
            _LOGGER.warning("Failed to fetch station details for %s: %s", station_id, err)
        return None

    async def async_get_stations(
        self, station_id: int | None = None
    ) -> list[StationSummary]:
        """Fetch list of solar stations accessible by the account."""
        if station_id is not None:
            station = await self.async_get_station(station_id)
            return [station] if station else []

        # 1. Try paged station list endpoint (standard Hoymiles API)
        endpoint = ENDPOINT_STATION_PAGE
        payloads_to_try = [
            {"page_size": 100, "page_num": 1},
            {"page_size": 20, "page": 1},
            {},
        ]

        for payload in payloads_to_try:
            try:
                response = await self._request(endpoint, payload)
                data = response.get("data", {})

                items: list[dict[str, Any]] = []
                if isinstance(data, list):
                    items = data
                elif isinstance(data, dict):
                    items = data.get("list", [])
                    if not items and "id" in data:
                        items = [data]

                stations: list[StationSummary] = []
                for item in items:
                    if not isinstance(item, dict) or "id" not in item:
                        continue
                    stations.append(
                        StationSummary(
                            id=int(item["id"]),
                            name=item.get("station_name") or item.get("name") or f"Plant {item['id']}",
                            capacity=float(item.get("capacity") or item.get("capacitor") or 0.0),
                            status=int(item.get("status", 0)),
                            timezone=str(item.get("timezone") or item.get("tz_name") or ""),
                            address=str(item.get("address") or item.get("location") or ""),
                        )
                    )

                if stations:
                    return stations
            except Exception as err:
                _LOGGER.warning(
                    "Station discovery attempt on %s with payload %s failed: %s",
                    endpoint,
                    payload,
                    err,
                )

        return []

    async def async_get_real_data(self, station_id: int) -> StationRealData:
        """Fetch real-time metrics for a station."""
        endpoint = ENDPOINT_REAL_DATA.get(self.api_version, ENDPOINT_REAL_DATA["0"])
        payload = {
            "sid": int(station_id),
            "station_id": int(station_id),
            "id": int(station_id),
        }

        response = await self._request(endpoint, payload)
        raw_data = response.get("data")
        data = raw_data if isinstance(raw_data, dict) else {}

        real_power = float(data.get("real_power") or 0.0)
        today_eq = float(data.get("today_eq") or 0.0)
        month_eq = float(data.get("month_eq") or 0.0)
        total_eq = float(data.get("total_eq") or 0.0)
        co2 = float(data.get("co2_emission_reduction") or data.get("co2_emission") or 0.0)
        tree = float(data.get("plant_tree") or data.get("tree_planted") or 0.0)
        data_time = str(data.get("data_time") or data.get("last_data_time") or "")

        return StationRealData(
            station_id=int(station_id),
            real_power_w=real_power,
            today_energy_kwh=today_eq,
            month_energy_kwh=month_eq,
            total_energy_kwh=total_eq,
            co2_reduction_kg=co2,
            tree_planted=tree,
            data_time=data_time,
            raw=data,
        )

    async def async_get_devices(self, station_id: int) -> PlantDevices:
        """Fetch hardware and micro-inverter device tree for a station."""
        endpoint = ENDPOINT_DEVICE_TREE.get(self.api_version, ENDPOINT_DEVICE_TREE["0"])
        payload = {
            "id": int(station_id),
            "station_id": int(station_id),
            "sid": int(station_id),
        }

        response = await self._request(endpoint, payload)
        raw_tree_data = response.get("data")
        raw_tree = raw_tree_data if isinstance(raw_tree_data, list) else []

        micro_inverters: list[MicroInverterInfo] = []
        self._parse_device_tree_items(raw_tree, micro_inverters)

        return PlantDevices(
            station_id=int(station_id),
            micro_inverters=micro_inverters,
            raw_tree=raw_tree,
        )

    def _parse_device_tree_items(
        self, items: list[dict[str, Any]], out_micros: list[MicroInverterInfo]
    ) -> None:
        """Recursively extract micro-inverters from the device hierarchy."""
        for item in items:
            if not isinstance(item, dict):
                continue

            # dev_type: 3 = micro-inverter, 6 = hybrid inverter
            dev_type = item.get("type")
            item_text = str(item.get("text", "")).lower()
            if dev_type in (3, 6) or any(k in item_text for k in ("micro", "inverter")):
                micro_id = item.get("id")
                sn = str(item.get("sn") or micro_id)
                model = str(item.get("model_no") or "")
                soft_ver = str(item.get("soft_ver") or "")
                warn_data = item.get("warn_data") or {}

                connected = True
                has_warn = False
                if isinstance(warn_data, dict):
                    connected = warn_data.get("connect", True)
                    has_warn = warn_data.get("warn", False)

                out_micros.append(
                    MicroInverterInfo(
                        id=int(micro_id),
                        sn=sn,
                        model=model,
                        firmware_version=soft_ver,
                        connected=connected,
                        has_warning=has_warn,
                    )
                )

            # Recurse through children
            children = item.get("children", [])
            if isinstance(children, list) and children:
                self._parse_device_tree_items(children, out_micros)

    async def async_get_micro_details(
        self, micro_id: int, station_id: int
    ) -> tuple[bool, int, str]:
        """Fetch real-time connectivity and alarms for a micro-inverter.

        Returns (connected, alarm_code, alarm_message).
        """
        endpoint = ENDPOINT_MICRO_DETAILS.get(
            self.api_version, ENDPOINT_MICRO_DETAILS["0"]
        )
        payload = {
            "id": int(micro_id),
            "dev_id": int(micro_id),
            "station_id": int(station_id),
            "sid": int(station_id),
        }

        response = await self._request(endpoint, payload)
        raw_data = response.get("data")
        data = raw_data if isinstance(raw_data, dict) else {}

        connected = bool(data.get("net_state", 1))
        raw_warn = data.get("warn_list")
        warn_list = raw_warn if isinstance(raw_warn, list) else []

        alarm_code = 0
        alarm_message = ""
        if warn_list:
            first_warn = warn_list[0]
            if isinstance(first_warn, dict):
                alarm_code = int(first_warn.get("err_code", 0))
                parts = [
                    first_warn.get("wd1", "").strip(),
                    first_warn.get("wdd1", "").strip(),
                    first_warn.get("wdd2", "").strip(),
                    first_warn.get("wd2", "").strip(),
                ]
                alarm_message = " ".join([p for p in parts if p and p != "-"])

        return connected, alarm_code, alarm_message

