"""Pure Python fuel route planning algorithm.

Calculates optimal fuel stops for a road trip given vehicle constraints,
route geometry, and candidate fuel stations.
Zero Django dependencies to facilitate fast, isolated unit testing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# Vehicle and optimization constants
MAX_RANGE_MILES = 500.0
MPG = 10.0
TANK_CAPACITY_GALLONS = MAX_RANGE_MILES / MPG  # 50.0 gallons
MAX_DETOUR_MILES = 10.0

# Conversion constants
EARTH_RADIUS_MILES = 3958.7613
METERS_PER_MILE = 1609.344


class InfeasibleRouteError(Exception):
    """Raised when distance between reachable stations or points exceeds vehicle range."""

    def __init__(self, gap_miles: float, from_location: str, to_location: str):
        self.gap_miles = round(gap_miles, 1)
        self.from_location = from_location
        self.to_location = to_location
        super().__init__(
            f"Route not feasible: gap of {self.gap_miles} miles exceeds 500-mile range "
            f"between {from_location} and {to_location}"
        )


@dataclass
class PlannedFuelStop:
    """Represents a planned fuel stop along the route."""

    stop_number: int
    truckstop_name: str
    address: str
    city: str
    state: str
    retail_price_per_gallon: float
    gallons_purchased: float
    cost_usd: float
    latitude: float
    longitude: float
    distance_from_start_miles: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stop_number": self.stop_number,
            "truckstop_name": self.truckstop_name,
            "address": self.address,
            "city": self.city,
            "state": self.state,
            "retail_price_per_gallon": round(self.retail_price_per_gallon, 3),
            "gallons_purchased": round(self.gallons_purchased, 2),
            "cost_usd": round(self.cost_usd, 2),
            "latitude": round(self.latitude, 6),
            "longitude": round(self.longitude, 6),
            "distance_from_start_miles": round(self.distance_from_start_miles, 1),
        }


@dataclass
class FuelPlanResult:
    """Summary of complete fuel stop itinerary and costs."""

    total_distance_miles: float
    total_fuel_cost_usd: float
    gallons_used: float
    fuel_stops: List[PlannedFuelStop]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_distance_miles": round(self.total_distance_miles, 1),
            "total_fuel_cost_usd": round(self.total_fuel_cost_usd, 2),
            "gallons_used": round(self.gallons_used, 2),
            "fuel_stops": [stop.to_dict() for stop in self.fuel_stops],
        }


def haversine_distance(coord1: Tuple[float, float], coord2: Tuple[float, float]) -> float:
    """Calculate Great Circle distance in miles between (lat1, lon1) and (lat2, lon2)."""
    lat1, lon1 = math.radians(coord1[0]), math.radians(coord1[1])
    lat2, lon2 = math.radians(coord2[0]), math.radians(coord2[1])

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = math.sin(dlat / 2.0) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_RADIUS_MILES * c


def point_to_segment_distance(
    p_lat: float,
    p_lon: float,
    a_lat: float,
    a_lon: float,
    b_lat: float,
    b_lon: float,
) -> Tuple[float, float]:
    """Calculate distance in miles from point P to line segment AB, and projection fraction t in [0, 1].

    Uses equirectangular flat-Earth projection centered around the segment,
    which is fast and accurate for segments under 50 miles.
    """
    mid_lat_rad = math.radians((a_lat + b_lat) / 2.0)
    cos_lat = math.cos(mid_lat_rad)
    miles_per_deg_lat = 69.0
    miles_per_deg_lon = 69.0 * cos_lat

    # Convert to local Cartesian coordinates (miles) with A as origin
    px = (p_lon - a_lon) * miles_per_deg_lon
    py = (p_lat - a_lat) * miles_per_deg_lat

    bx = (b_lon - a_lon) * miles_per_deg_lon
    by = (b_lat - a_lat) * miles_per_deg_lat

    seg_len_sq = bx * bx + by * by
    if seg_len_sq < 1e-9:
        dist = math.hypot(px, py)
        return dist, 0.0

    t = (px * bx + py * by) / seg_len_sq
    t_clamped = max(0.0, min(1.0, t))

    proj_x = t_clamped * bx
    proj_y = t_clamped * by

    dist = math.hypot(px - proj_x, py - proj_y)
    return dist, t_clamped


def project_stations_onto_route(
    coordinates: List[Tuple[float, float]],  # List of [lon, lat] from GeoJSON
    stations: List[Any],
    max_detour_miles: float = MAX_DETOUR_MILES,
    total_route_distance_miles: Optional[float] = None,
) -> List[Tuple[float, Any]]:
    """Filter stations within max_detour_miles and project onto route.

    Returns:
        List of tuples: (distance_from_start_miles, station), sorted by distance.
    """
    if len(coordinates) < 2 or not stations:
        return []

    # GeoJSON coordinates are [lon, lat]; convert to (lat, lon) for geometry
    lat_lon_pts = [(pt[1], pt[0]) for pt in coordinates]

    # 1. Bounding box prefilter
    lats = [p[0] for p in lat_lon_pts]
    lons = [p[1] for p in lat_lon_pts]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)

    # Detour buffer in degrees (~0.25 deg lat, ~0.35 deg lon)
    lat_buf = max_detour_miles / 69.0 + 0.05
    cos_mid = max(0.1, math.cos(math.radians((min_lat + max_lat) / 2.0)))
    lon_buf = (max_detour_miles / (69.0 * cos_mid)) + 0.05

    candidate_stations = []
    for s in stations:
        lat = getattr(s, "latitude", None)
        lon = getattr(s, "longitude", None)
        if lat is None or lon is None:
            continue
        if (min_lat - lat_buf <= lat <= max_lat + lat_buf) and (
            min_lon - lon_buf <= lon <= max_lon + lon_buf
        ):
            candidate_stations.append(s)

    if not candidate_stations:
        return []

    # 2. Cumulative segment distances
    cum_distances = [0.0]
    seg_lengths = []
    for i in range(len(lat_lon_pts) - 1):
        seg_len = haversine_distance(lat_lon_pts[i], lat_lon_pts[i + 1])
        seg_lengths.append(seg_len)
        cum_distances.append(cum_distances[-1] + seg_len)

    total_polyline_dist = cum_distances[-1]
    if total_polyline_dist <= 0:
        return []

    scale_factor = (
        (total_route_distance_miles / total_polyline_dist)
        if total_route_distance_miles
        else 1.0
    )

    projected: List[Tuple[float, Any]] = []

    for station in candidate_stations:
        s_lat = station.latitude
        s_lon = station.longitude

        best_dist = float("inf")
        best_route_dist = 0.0

        for i in range(len(lat_lon_pts) - 1):
            a_lat, a_lon = lat_lon_pts[i]
            b_lat, b_lon = lat_lon_pts[i + 1]

            # Fast bounding box check on individual segment
            seg_min_lat = min(a_lat, b_lat) - lat_buf
            seg_max_lat = max(a_lat, b_lat) + lat_buf
            if not (seg_min_lat <= s_lat <= seg_max_lat):
                continue

            dist, t = point_to_segment_distance(s_lat, s_lon, a_lat, a_lon, b_lat, b_lon)
            if dist < best_dist:
                best_dist = dist
                best_route_dist = cum_distances[i] + t * seg_lengths[i]

        if best_dist <= max_detour_miles:
            dist_from_start = best_route_dist * scale_factor
            projected.append((dist_from_start, station))

    # Sort by distance from start
    projected.sort(key=lambda x: x[0])

    # Deduplicate: if multiple stations are within 1.0 mile of each other,
    # keep the one with the lowest retail price
    deduped: List[Tuple[float, Any]] = []
    for dist, st in projected:
        if deduped and abs(dist - deduped[-1][0]) < 1.0:
            if st.retail_price < deduped[-1][1].retail_price:
                deduped[-1] = (dist, st)
        else:
            deduped.append((dist, st))

    return deduped


def plan_fuel_stops(
    total_distance_miles: float,
    projected_stations: List[Tuple[float, Any]],
    start_name: str = "Start",
    finish_name: str = "Destination",
    max_range_miles: float = MAX_RANGE_MILES,
    mpg: float = MPG,
) -> FuelPlanResult:
    """Compute cost-optimal fuel stops along the route.

    Vehicle begins with a full tank (500 miles range).
    At each step:
      - If destination can be reached with current fuel, drive directly to destination.
      - Otherwise, inspect reachable stations.
      - Move to the optimal fuel stop.
      - Purchase only enough fuel to reach the next cheaper station, or fill tank.
      - If distance gap between reachable stations/destination exceeds 500 miles,
        raise InfeasibleRouteError.
    """
    total_gallons_used = round(total_distance_miles / mpg, 2)

    # If entire trip is within initial tank range, 0 stops needed
    if total_distance_miles <= max_range_miles:
        return FuelPlanResult(
            total_distance_miles=total_distance_miles,
            total_fuel_cost_usd=0.0,
            gallons_used=total_gallons_used,
            fuel_stops=[],
        )

    # Stations strictly along the route (between 0 and total_distance)
    valid_stations = [
        (d, s) for d, s in projected_stations if 0.0 < d < total_distance_miles
    ]

    current_dist = 0.0
    current_fuel_gal = max_range_miles / mpg  # Start with full tank (50 gal)
    planned_stops: List[PlannedFuelStop] = []
    current_loc_name = start_name

    while (total_distance_miles - current_dist) > (current_fuel_gal * mpg + 1e-4):
        reachable_range = current_fuel_gal * mpg
        max_reach_dist = current_dist + reachable_range

        # All stations within reachable distance from current position
        reachable_candidates = [
            (d, s) for d, s in valid_stations if current_dist < d <= max_reach_dist
        ]

        if not reachable_candidates:
            # Cannot reach any station! Gap exceeds range
            next_ahead = [(d, s) for d, s in valid_stations if d > current_dist]
            if next_ahead:
                next_d, next_s = next_ahead[0]
                gap = next_d - current_dist
                to_name = f"{getattr(next_s, 'city', '')}, {getattr(next_s, 'state', '')}".strip()
                if not to_name.strip(","):
                    to_name = getattr(next_s, "truckstop_name", "Next Station")
            else:
                gap = total_distance_miles - current_dist
                to_name = finish_name

            raise InfeasibleRouteError(gap, current_loc_name, to_name)

        # Decide which station to stop at:
        # Standard greedy: Look for the cheapest station within reach.
        best_candidate = min(
            reachable_candidates,
            key=lambda item: (item[1].retail_price, -item[0]),
        )

        stop_dist, stop_station = best_candidate
        dist_driven = stop_dist - current_dist
        gallons_burned = dist_driven / mpg
        fuel_remaining = max(0.0, current_fuel_gal - gallons_burned)

        # At this stop, determine how many gallons to purchase:
        full_tank_reach = stop_dist + max_range_miles
        stations_ahead = [
            (d, s) for d, s in valid_stations if stop_dist < d <= full_tank_reach
        ]

        # Is there a cheaper station ahead within a full tank?
        cheaper_ahead = [
            (d, s)
            for d, s in stations_ahead
            if s.retail_price < stop_station.retail_price
        ]

        if cheaper_ahead:
            # Buy only enough to reach the first cheaper station ahead
            target_dist, _ = cheaper_ahead[0]
            dist_to_cheaper = target_dist - stop_dist
            fuel_needed = dist_to_cheaper / mpg
            gallons_to_buy = max(0.0, fuel_needed - fuel_remaining)
        elif total_distance_miles <= full_tank_reach:
            # Destination is reachable within a full tank, and no station ahead is cheaper.
            # Buy just enough to reach the destination!
            dist_to_dest = total_distance_miles - stop_dist
            fuel_needed = dist_to_dest / mpg
            gallons_to_buy = max(0.0, fuel_needed - fuel_remaining)
        else:
            # No cheaper station ahead within reach and cannot reach dest: fill the tank!
            gallons_to_buy = max(0.0, (max_range_miles / mpg) - fuel_remaining)

        # If vehicle still cannot reach next point, fill up
        if gallons_to_buy < 1e-4 and (stop_dist + fuel_remaining * mpg < total_distance_miles):
            gallons_to_buy = max(0.0, (max_range_miles / mpg) - fuel_remaining)

        if gallons_to_buy > 1e-3:
            stop_cost = round(gallons_to_buy * stop_station.retail_price, 2)
            planned_stop = PlannedFuelStop(
                stop_number=len(planned_stops) + 1,
                truckstop_name=stop_station.truckstop_name,
                address=stop_station.address,
                city=stop_station.city,
                state=stop_station.state,
                retail_price_per_gallon=stop_station.retail_price,
                gallons_purchased=round(gallons_to_buy, 2),
                cost_usd=stop_cost,
                latitude=stop_station.latitude,
                longitude=stop_station.longitude,
                distance_from_start_miles=round(stop_dist, 1),
            )
            planned_stops.append(planned_stop)

        # Advance state
        current_dist = stop_dist
        current_fuel_gal = fuel_remaining + gallons_to_buy
        current_loc_name = f"{stop_station.city}, {stop_station.state}"

    # Sum per-stop costs for total cost
    total_cost = round(sum(s.cost_usd for s in planned_stops), 2)

    return FuelPlanResult(
        total_distance_miles=total_distance_miles,
        total_fuel_cost_usd=total_cost,
        gallons_used=total_gallons_used,
        fuel_stops=planned_stops,
    )
