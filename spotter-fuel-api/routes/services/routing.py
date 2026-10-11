"""Routing service using Open Source Routing Machine (OSRM).

Provides driving route geometry and total distance with exactly one call per request.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple
import requests

from .geocoding import UpstreamServiceError

logger = logging.getLogger(__name__)

METERS_TO_MILES = 0.000621371192237334
REQUEST_TIMEOUT_SECONDS = 10
OSRM_BASE_URL = "https://router.project-osrm.org/route/v1/driving"


class RoutingError(Exception):
    """Raised when routing fails or no path exists between coordinates."""

    pass


@dataclass
class RouteResult:
    """Contains calculated route distance, duration, and GeoJSON geometry."""

    total_distance_miles: float
    total_duration_seconds: float
    coordinates: List[Tuple[float, float]]  # List of [lon, lat] points
    geojson: Dict[str, Any]


class BaseRoutingProvider:
    """Interface for driving route providers."""

    def get_route(
        self,
        start_coords: Tuple[float, float],
        finish_coords: Tuple[float, float],
    ) -> RouteResult:
        raise NotImplementedError


class OSRMRoutingProvider(BaseRoutingProvider):
    """OSRM implementation for driving route calculation."""

    def __init__(self, base_url: str = OSRM_BASE_URL, timeout: int = REQUEST_TIMEOUT_SECONDS):
        self.base_url = base_url
        self.timeout = timeout

    def get_route(
        self,
        start_coords: Tuple[float, float],
        finish_coords: Tuple[float, float],
    ) -> RouteResult:
        """Fetch driving route between start (lat, lon) and finish (lat, lon).

        Note: OSRM expects coordinates in {lon},{lat} order.
        """
        lat1, lon1 = start_coords
        lat2, lon2 = finish_coords

        url = (
            f"{self.base_url}/{lon1:.6f},{lat1:.6f};{lon2:.6f},{lat2:.6f}"
            "?overview=full&geometries=geojson&steps=false"
        )

        headers = {
            "User-Agent": "spotter-fuel-api-assessment",
            "Accept": "application/json",
        }

        try:
            response = requests.get(url, headers=headers, timeout=self.timeout)
        except (requests.Timeout, requests.ConnectionError) as e:
            logger.error(f"OSRM routing request failed: {e}")
            raise UpstreamServiceError(f"OSRM routing service unavailable or timed out: {e}") from e
        except requests.RequestException as e:
            logger.error(f"OSRM error: {e}")
            raise UpstreamServiceError(f"OSRM routing request failed: {e}") from e

        # OSRM returns HTTP 400 when coordinates cannot be routed (e.g. islands / NoRoute)
        if response.status_code == 400:
            try:
                err_data = response.json()
                msg = err_data.get("message", "Impossible route between points")
            except Exception:
                msg = "No driving route between coordinates"
            raise RoutingError(f"Route not feasible: {msg}")

        if response.status_code != 200:
            logger.error(f"OSRM returned HTTP {response.status_code}")
            raise UpstreamServiceError(f"OSRM returned HTTP {response.status_code}")

        data = response.json()
        if data.get("code") != "Ok" or not data.get("routes"):
            error_msg = data.get("message", "No route found between points")
            logger.warning(f"OSRM routing error: {error_msg}")
            raise RoutingError(f"Route not feasible: {error_msg}")

        primary_route = data["routes"][0]
        distance_meters = float(primary_route.get("distance", 0.0))
        duration_seconds = float(primary_route.get("duration", 0.0))
        geometry = primary_route.get("geometry", {})
        raw_coords = geometry.get("coordinates", [])

        # Format coordinates as list of [lon, lat] tuples
        coords: List[Tuple[float, float]] = [
            (float(pt[0]), float(pt[1])) for pt in raw_coords if len(pt) >= 2
        ]

        distance_miles = round(distance_meters * METERS_TO_MILES, 2)

        return RouteResult(
            total_distance_miles=distance_miles,
            total_duration_seconds=duration_seconds,
            coordinates=coords,
            geojson={
                "type": "LineString",
                "coordinates": coords,
            },
        )


# Default provider instance
_default_provider = OSRMRoutingProvider()


def get_route(
    start_coords: Tuple[float, float],
    finish_coords: Tuple[float, float],
    provider: BaseRoutingProvider = _default_provider,
) -> RouteResult:
    """Public helper to get route using configured provider."""
    return provider.get_route(start_coords, finish_coords)
