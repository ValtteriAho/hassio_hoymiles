"""Constants for the Hoymiles Solar Cloud integration."""

from __future__ import annotations

from typing import Final
from homeassistant.const import Platform

DOMAIN: Final = "hoymiles_cloud"

# Platforms
PLATFORMS: Final = [Platform.SENSOR, Platform.BINARY_SENSOR]

# Configuration keys
CONF_PLANT_ID: Final = "plant_id"
CONF_PLANT_NAME: Final = "plant_name"
CONF_BASE_URL: Final = "base_url"
CONF_REGION: Final = "region"
CONF_USE_ESTAR: Final = "use_estar"
CONF_SCAN_INTERVAL: Final = "scan_interval"

# Region identifiers
REGION_AUTO: Final = "auto"
REGION_GLOBAL: Final = "global"
REGION_EUROPE: Final = "europe"
REGION_ESTAR: Final = "estar"

# Defaults & Gateways
DEFAULT_SCAN_INTERVAL: Final = 480  # 8 minutes (safe for Hoymiles cloud rate limits)
MIN_SCAN_INTERVAL: Final = 180     # 3 minutes
DEFAULT_BASE_URL: Final = "https://neapi.hoymiles.com/"
API_EU_BASE_URL: Final = "https://euapi.hoymiles.com/"
ESTAR_BASE_URL: Final = "https://monitor.estarpower.com/platform/api/gateway/"

REGION_URL_MAP: Final = {
    REGION_AUTO: DEFAULT_BASE_URL,
    REGION_GLOBAL: DEFAULT_BASE_URL,
    REGION_EUROPE: API_EU_BASE_URL,
    REGION_ESTAR: ESTAR_BASE_URL,
}


