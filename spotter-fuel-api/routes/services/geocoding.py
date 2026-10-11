"""Geocoding service using OpenStreetMap Nominatim API.

Includes in-memory caching to eliminate duplicate outbound requests
and adheres strictly to Nominatim usage policies.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple
import requests

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
DEFAULT_USER_AGENT = "spotter-fuel-api-assessment"
REQUEST_TIMEOUT_SECONDS = 10

# In-memory geocoding cache: normalized query -> (lat, lon)
_GEOCODE_CACHE: Dict[str, Tuple[float, float]] = {}


class GeocodingError(Exception):
    """Raised when geocoding fails or location cannot be found."""

    pass


class UpstreamServiceError(Exception):
    """Raised when upstream API (Nominatim/OSRM) times out or fails."""

    pass


def normalize_query(query: str) -> str:
    """Normalize query string for consistent cache key lookup."""
    return " ".join(query.strip().lower().split())


def geocode_location(location: str, user_agent: str = DEFAULT_USER_AGENT) -> Tuple[float, float]:
    """Geocode a location string (e.g. 'Denver, CO') to (latitude, longitude).

    Uses in-memory cache to prevent redundant HTTP calls.

    Raises:
        UpstreamServiceError: If Nominatim times out or returns HTTP 5xx.
        GeocodingError: If the location cannot be resolved.
    """
    clean_query = normalize_query(location)
    if clean_query in _GEOCODE_CACHE:
        logger.debug(f"Geocode cache hit for: {location}")
        return _GEOCODE_CACHE[clean_query]

    # If query does not mention USA or United States, append USA
    query_param = location.strip()
    if "usa" not in query_param.lower() and "united states" not in query_param.lower():
        query_param = f"{query_param}, USA"

    headers = {
        "User-Agent": user_agent,
        "Accept": "application/json",
    }
    params = {
        "q": query_param,
        "format": "json",
        "limit": 1,
    }

    try:
        response = requests.get(
            NOMINATIM_URL,
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except (requests.Timeout, requests.ConnectionError) as e:
        logger.error(f"Nominatim upstream error for '{location}': {e}")
        raise UpstreamServiceError(f"Nominatim geocoding service unavailable or timed out: {e}") from e
    except requests.RequestException as e:
        logger.error(f"Nominatim request error for '{location}': {e}")
        raise UpstreamServiceError(f"Nominatim geocoding request failed: {e}") from e

    if response.status_code != 200:
        logger.error(f"Nominatim returned status {response.status_code} for '{location}'")
        raise UpstreamServiceError(f"Nominatim returned HTTP {response.status_code}")

    data = response.json()
    if not data or not isinstance(data, list) or len(data) == 0:
        raise GeocodingError(f"Could not find coordinates for location: '{location}'")

    first_result = data[0]
    try:
        lat = float(first_result["lat"])
        lon = float(first_result["lon"])
    except (KeyError, ValueError) as e:
        raise GeocodingError(f"Invalid coordinate format from Nominatim: {e}") from e

    coords = (lat, lon)
    _GEOCODE_CACHE[clean_query] = coords
    return coords


def clear_geocode_cache() -> None:
    """Clear the in-memory geocoding cache (useful for testing)."""
    global _GEOCODE_CACHE
    _GEOCODE_CACHE = {}


def set_cached_geocode(location: str, coords: Tuple[float, float]) -> None:
    """Pre-seed the in-memory geocoding cache."""
    _GEOCODE_CACHE[normalize_query(location)] = coords
