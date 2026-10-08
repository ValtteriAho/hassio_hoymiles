"""Asynchronous Hoymiles S-Miles Cloud API client."""

from __future__ import annotations

import asyncio
import base64
import binascii
from dataclasses import dataclass, field
import hashlib
import json
import logging
from typing import Any
from urllib.parse import urlparse

import aiohttp

try:
    from argon2.low_level import Type, hash_secret_raw

    ARGON2_AVAILABLE = True
except ImportError:
    Type = None
    hash_secret_raw = None
    ARGON2_AVAILABLE = False

_LOGGER = logging.getLogger(__name__)

# Base URLs
DEFAULT_BASE_URL = "https://neapi.hoymiles.com/"
API_EU_BASE_URL = "https://euapi.hoymiles.com/"
ESTAR_BASE_URL = "https://monitor.estarpower.com/platform/api/gateway/"

# Endpoints
ARGON_PRE_INSP_API = "iam/pub/3/auth/pre-insp"
ARGON_LOGIN_API = "iam/pub/3/auth/login"
LEGACY_LOGIN_API = "iam/pub/0/auth/login"

ENDPOINT_STATION_FIND = {
    "0": "pvm/api/0/station/find",
    "1": "pvm/api/0/station/find",
}

ENDPOINT_REAL_DATA = {
    "0": "pvm-data/api/0/station/data/count_station_real_data",
    "1": "pms/v1/station/data/count",
}

ENDPOINT_DEVICE_TREE = {
    "0": "pvm/api/0/station/select_device_of_tree",
    "1": "pms/v1/station/device/tree",
}

ENDPOINT_MICRO_DETAILS = {
    "0": "pvm/api/0/dev/micro/find",
    "1": "pms/v1/device/micro/find",
}

HEADER_JSON = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,"
        " like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}



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
    ) -> None:
        """Initialize the client."""
        self.username = username
        self.password = password
        self._custom_session = session
        self._session: aiohttp.ClientSession | None = session
        self.base_url = ESTAR_BASE_URL if use_estar else base_url
        self.estar_mode = use_estar
        self.token: str | None = None
        self.api_version: str = "1"
        self._lock = asyncio.Lock()

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
        self, base_url: str
    ) -> tuple[str | None, str | None, str | None]:
        """Run pre-inspect on the target base URL.

        Returns (nonce, salt, error_message).
        """
        url = base_url.rstrip("/") + "/" + ARGON_PRE_INSP_API
        session = await self._get_session()

        try:
            async with session.post(
                url,
                json={"u": self.username},
                headers=HEADER_JSON,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return None, None, f"HTTP status {resp.status} on pre-inspect"
                data = await resp.json()
        except Exception as err:
            return None, None, str(err)

        if str(data.get("status")) != "0":
            return None, None, data.get("message") or "Pre-inspect returned non-zero"

        insp_data = data.get("data", {})
        nonce = insp_data.get("n")
        salt = insp_data.get("a")
        return nonce, salt, None

    async def _login_v3_attempt(
        self, base_url: str, credential_hash: str, nonce: str
    ) -> tuple[str | None, str | None]:
        """Attempt a single v3 login request.

        Returns (token, error_message).
        """
        login_url = base_url.rstrip("/") + "/" + ARGON_LOGIN_API
        session = await self._get_session()
        login_payload = {"u": self.username, "ch": credential_hash, "n": nonce}

        try:
            async with session.post(
                login_url,
                json=login_payload,
                headers=HEADER_JSON,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return None, f"HTTP status {resp.status} on login"
                login_data = await resp.json()
        except Exception as err:
            return None, str(err)

        if str(login_data.get("status")) == "0":
            token = login_data.get("data", {}).get("token", "")
            if token:
                return token, None

        return None, login_data.get("message") or "Login failed"

    async def _async_v3_login(
        self, base_url: str
    ) -> tuple[str | None, str | None]:
        """Execute modern v3 login supporting both salted and unsalted flows."""
        nonce, salt, err = await self._pre_inspect_v3(base_url)
        if not nonce:
            return None, err or "Failed to retrieve pre-inspection nonce"

        # Case 1: Server provided a salt -> Compute Argon2id hash
        salt_str = (salt or "").strip()
        if salt_str and ARGON2_AVAILABLE:
            try:
                salt_bytes = self._decode_v3_salt(salt_str)
                ch = await self._async_argon_compute_challenge(salt_bytes)
                token, login_err = await self._login_v3_attempt(base_url, ch, nonce)
                if token:
                    return token, None
                _LOGGER.debug(
                    "Argon2 salted login failed on %s: %s", base_url, login_err
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
                retry_nonce, _, retry_err = await self._pre_inspect_v3(base_url)
                if not retry_nonce:
                    continue
                nonce = retry_nonce

            token, login_err = await self._login_v3_attempt(
                base_url, cand_hash, nonce
            )
            if token:
                _LOGGER.debug(
                    "v3 unsalted login succeeded on %s with variant %s", base_url, name
                )
                return token, None
            last_error = login_err or last_error

        return None, last_error

    async def _async_legacy_login(
        self, base_url: str
    ) -> tuple[str | None, str | None]:
        """Execute legacy MD5 authentication."""
        pass_hash = hashlib.md5(self.password.encode("utf-8")).hexdigest()
        url = base_url.rstrip("/") + "/" + LEGACY_LOGIN_API
        session = await self._get_session()

        payload = {
            "password": pass_hash,
            "user_name": self.username,
        }
        try:
            async with session.post(
                url, json=payload, headers=HEADER_JSON, timeout=aiohttp.ClientTimeout(total=15)
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
            urls_to_try = [self.base_url]
            # Automatically try EU gateway as fallback if default global was configured
            if (
                self.base_url.rstrip("/") == DEFAULT_BASE_URL.rstrip("/")
                and not self.estar_mode
            ):
                urls_to_try.append(API_EU_BASE_URL)

            last_error: str = "Invalid credentials"
            for target_url in urls_to_try:
                _LOGGER.debug("Attempting authentication against: %s", target_url)

                # 1. Try modern v3 login (salted Argon2 and unsalted candidates)
                token, err = await self._async_v3_login(target_url)
                if token:
                    self.token = token
                    self.base_url = target_url
                    self.api_version = "1"
                    _LOGGER.info("Authenticated successfully on %s (v3)", target_url)
                    return token
                if err:
                    last_error = err

                # 2. Try legacy v0 MD5 login as fallback
                token, err_leg = await self._async_legacy_login(target_url)
                if token:
                    self.token = token
                    self.base_url = target_url
                    self.api_version = "0"
                    _LOGGER.info("Authenticated successfully on %s (legacy v0)", target_url)
                    return token
                if err_leg:
                    last_error = err_leg

            raise HoymilesAuthError(f"{last_error}")

    async def async_get_stations(self) -> list[StationSummary]:
        """Fetch list of solar stations accessible by the account."""
        endpoint = ENDPOINT_STATION_FIND.get(self.api_version, ENDPOINT_STATION_FIND["0"])
        # Some accounts use {"id": ""} or empty payload
        response = await self._request(endpoint, {})
        data = response.get("data", {})

        stations: list[StationSummary] = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("list", [data]) if "list" in data else [data]
        else:
            items = []

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

        return stations

    async def async_get_real_data(self, station_id: int) -> StationRealData:
        """Fetch real-time metrics for a station."""
        endpoint = ENDPOINT_REAL_DATA.get(self.api_version, ENDPOINT_REAL_DATA["0"])
        payload = (
            {"station_id": int(station_id)}
            if self.api_version == "1"
            else {"sid": str(station_id)}
        )

        response = await self._request(endpoint, payload)
        data = response.get("data", {})

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
        payload = (
            {"station_id": int(station_id)}
            if self.api_version == "1"
            else {"id": str(station_id)}
        )

        response = await self._request(endpoint, payload)
        raw_tree = response.get("data", [])

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
        payload = (
            {"station_id": int(station_id)}
            if self.api_version == "1"
            else {"id": str(micro_id)}
        )

        response = await self._request(endpoint, payload)
        data = response.get("data", {})

        connected = bool(data.get("net_state", 1))
        warn_list = data.get("warn_list", [])

        alarm_code = 0
        alarm_message = ""
        if isinstance(warn_list, list) and warn_list:
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

