"""Station data loader and coordinate cache service.

Loads fuel prices from CSV and coordinates from station_coords.json.
Maintains an in-memory cache to ensure fast subsequent lookups.
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Base paths relative to the routes app
ROUTES_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROUTES_DIR / "data"
CSV_PATH = DATA_DIR / "fuel-prices-for-be-assessment.csv"
COORDS_PATH = DATA_DIR / "station_coords.json"


@dataclass
class FuelStation:
    """Represents a truckstop fuel station with price and location data."""

    opis_id: str
    truckstop_name: str
    address: str
    city: str
    state: str
    rack_id: str
    retail_price: float
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    @property
    def city_state_key(self) -> str:
        """Standardized lookup key for city/state coordinates."""
        return f"{self.city.strip().lower()}, {self.state.strip().lower()}"

    def to_dict(self) -> dict:
        """Serialize station to dictionary."""
        return {
            "opis_id": self.opis_id,
            "truckstop_name": self.truckstop_name,
            "address": self.address,
            "city": self.city,
            "state": self.state,
            "retail_price_per_gallon": self.retail_price,
            "latitude": self.latitude,
            "longitude": self.longitude,
        }


# Module-level caches
_STATIONS_CACHE: Optional[List[FuelStation]] = None
_COORDS_CACHE: Optional[Dict[str, Tuple[float, float]]] = None


def normalize_city_state(city: str, state: str) -> str:
    """Normalize city and state string to a consistent lowercase key."""
    return f"{city.strip().lower()}, {state.strip().lower()}"


def load_station_coordinates(force_reload: bool = False) -> Dict[str, Tuple[float, float]]:
    """Load cached geocoded coordinates from station_coords.json.

    Returns:
        Dict mapping normalized 'city, state' to (latitude, longitude).
    """
    global _COORDS_CACHE
    if _COORDS_CACHE is not None and not force_reload:
        return _COORDS_CACHE

    coords_map: Dict[str, Tuple[float, float]] = {}
    if COORDS_PATH.exists():
        try:
            with open(COORDS_PATH, mode="r", encoding="utf-8") as f:
                data = json.load(f)
                for key, val in data.items():
                    norm_key = key.strip().lower()
                    if isinstance(val, (list, tuple)) and len(val) == 2:
                        coords_map[norm_key] = (float(val[0]), float(val[1]))
                    elif isinstance(val, dict) and "lat" in val and "lon" in val:
                        coords_map[norm_key] = (float(val["lat"]), float(val["lon"]))
        except Exception as e:
            logger.warning(f"Could not load station_coords.json: {e}")

    _COORDS_CACHE = coords_map
    return _COORDS_CACHE


def save_station_coordinates(coords_map: Dict[str, Tuple[float, float]]) -> None:
    """Save geocoded coordinates to station_coords.json atomically."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp_file = COORDS_PATH.with_suffix(".tmp")
    with open(temp_file, mode="w", encoding="utf-8") as f:
        json.dump(coords_map, f, indent=2, sort_keys=True)
    temp_file.replace(COORDS_PATH)

    global _COORDS_CACHE
    _COORDS_CACHE = dict(coords_map)


def load_stations(force_reload: bool = False) -> List[FuelStation]:
    """Load all fuel stations from CSV and attach coordinates if available.

    Cached at module level for sub-millisecond repeated queries.
    """
    global _STATIONS_CACHE
    if _STATIONS_CACHE is not None and not force_reload:
        return _STATIONS_CACHE

    coords_map = load_station_coordinates(force_reload=force_reload)
    stations: List[FuelStation] = []

    if not CSV_PATH.exists():
        logger.error(f"Fuel prices CSV not found at {CSV_PATH}")
        _STATIONS_CACHE = []
        return _STATIONS_CACHE

    with open(CSV_PATH, mode="r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header:
            return []

        for row in reader:
            if len(row) < 7:
                continue
            opis_id, name, address, city, state, rack_id, price_str = [c.strip() for c in row[:7]]
            try:
                retail_price = float(price_str)
            except ValueError:
                continue

            station = FuelStation(
                opis_id=opis_id,
                truckstop_name=name,
                address=address,
                city=city,
                state=state,
                rack_id=rack_id,
                retail_price=retail_price,
            )

            key = station.city_state_key
            if key in coords_map:
                station.latitude, station.longitude = coords_map[key]

            stations.append(station)

    _STATIONS_CACHE = stations
    return _STATIONS_CACHE


def get_geocoded_stations(force_reload: bool = False) -> List[FuelStation]:
    """Return all stations that have valid latitude and longitude coordinates."""
    all_stations = load_stations(force_reload=force_reload)
    return [s for s in all_stations if s.latitude is not None and s.longitude is not None]


def get_unique_city_states_from_csv() -> List[Tuple[str, str]]:
    """Return sorted unique (City, State) tuples from CSV."""
    if not CSV_PATH.exists():
        return []

    unique_pairs = set()
    with open(CSV_PATH, mode="r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if len(row) >= 5:
                city, state = row[3].strip(), row[4].strip()
                if city and state:
                    unique_pairs.add((city, state))

    return sorted(list(unique_pairs), key=lambda x: (x[1], x[0]))


def clear_cache() -> None:
    """Clear in-memory caches (useful for testing)."""
    global _STATIONS_CACHE, _COORDS_CACHE
    _STATIONS_CACHE = None
    _COORDS_CACHE = None
