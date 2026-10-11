"""Comprehensive test suite for the Fuel Route Planner API and Services.

Includes:
- Pure Python unit tests for optimal fuel planner with hand-calculated expected costs
- Infeasible route detection (> 500 mile gap -> 422)
- Input validation (missing/blank fields -> 400)
- Upstream failure handling (timeout -> 502)
- HTTP call budget verification (<= 3 cold, 1 warm)
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import requests
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from routes.services.geocoding import (
    clear_geocode_cache,
    geocode_location,
    set_cached_geocode,
    UpstreamServiceError,
)
from routes.services.planner import (
    InfeasibleRouteError,
    plan_fuel_stops,
)
from routes.services.stations import FuelStation, clear_cache

# Realistic coordinates following the US highway corridor between Denver and Dallas
CORRIDOR_POLYLINE = [
    [-104.9848, 39.7392],  # Denver, CO
    [-104.8253, 38.8339],  # Colorado Springs, CO
    [-104.6141, 38.2639],  # Pueblo, CO
    [-104.7809, 37.6235],  # Walsenburg, CO
    [-104.5005, 37.1693],  # Trinidad, CO
    [-103.1847, 36.4507],  # Clayton, NM
    [-102.5222, 36.0616],  # Dalhart, TX
    [-101.9713, 35.8614],  # Dumas, TX
    [-101.8371, 35.2072],  # Amarillo, TX
    [-100.2067, 34.5196],  # Childress, TX
    [-99.2860, 34.1513],   # Vernon, TX
    [-98.5020, 33.9004],   # Wichita Falls, TX
    [-97.5861, 33.2342],   # Decatur, TX
    [-97.3327, 32.7531],   # Fort Worth, TX
    [-96.7968, 32.7762],   # Dallas, TX
]


class DummyStation:
    """Helper dummy fuel station for unit testing."""

    def __init__(
        self,
        opis_id: str,
        name: str,
        city: str,
        state: str,
        price: float,
        lat: float = 35.0,
        lon: float = -100.0,
    ):
        self.opis_id = opis_id
        self.truckstop_name = name
        self.address = f"100 Main St, {city}"
        self.city = city
        self.state = state
        self.rack_id = "1"
        self.retail_price = price
        self.latitude = lat
        self.longitude = lon


class PurePlannerUnitTests(TestCase):
    """Unit tests for pure Python planner.py with hand-computed expected costs."""

    def test_zero_stops_when_under_max_range(self):
        """A trip of 400 miles requires 0 stops as full tank covers 500 miles."""
        stations = [
            (200.0, DummyStation("1", "Mid Station", "Midway", "TX", 3.25)),
        ]
        result = plan_fuel_stops(
            total_distance_miles=400.0,
            projected_stations=stations,
            start_name="Origin",
            finish_name="Dest",
        )
        self.assertEqual(len(result.fuel_stops), 0)
        self.assertEqual(result.total_fuel_cost_usd, 0.0)
        self.assertEqual(result.gallons_used, 40.0)
        self.assertEqual(result.total_distance_miles, 400.0)

    def test_single_stop_optimal_purchase(self):
        """Hand-computed test: Trip = 700 miles.

        - Start at mile 0 with 50 gal (500 mi range).
        - Stop 1 at mile 350 ($3.00/gal).
        - Arrives with 50 - 35 = 15 gal remaining.
        - Distance from mile 350 to destination = 350 miles (needs 35 gal).
        - Gallons to purchase = 35 - 15 = 20 gal.
        - Cost = 20 gal * $3.00 = $60.00.
        """
        station = DummyStation("1", "Halfway Truckstop", "Midtown", "TX", 3.00)
        stations = [(350.0, station)]

        result = plan_fuel_stops(
            total_distance_miles=700.0,
            projected_stations=stations,
            start_name="Denver, CO",
            finish_name="Dallas, TX",
        )

        self.assertEqual(len(result.fuel_stops), 1)
        stop = result.fuel_stops[0]
        self.assertEqual(stop.stop_number, 1)
        self.assertAlmostEqual(stop.distance_from_start_miles, 350.0, places=1)
        self.assertAlmostEqual(stop.gallons_purchased, 20.0, places=2)
        self.assertAlmostEqual(stop.cost_usd, 60.00, places=2)
        self.assertAlmostEqual(result.total_fuel_cost_usd, 60.00, places=2)
        self.assertAlmostEqual(result.gallons_used, 70.0, places=2)

    def test_multiple_stops_cheaper_station_ahead(self):
        """Hand-computed test: Trip = 900 miles.

        - Start at mile 0 with 50 gal.
        - Station 1 at mile 300 ($3.50/gal).
        - Station 2 at mile 550 ($2.50/gal).
        - From mile 0: Station 1 is reachable, Station 2 (550) is not.
        - Arrive at Station 1 with 50 - 30 = 20 gal.
        - Station 2 is 250 miles ahead (< 500 mi) and CHEAPER ($2.50 < $3.50).
        - Only buy fuel to reach Station 2: 25 gal needed - 20 gal left = 5 gal.
        - Cost at Station 1 = 5 * 3.50 = $17.50.
        - Arrive at Station 2 with 0 gal.
        - From Station 2 to dest = 350 miles (35 gal). No cheaper station ahead.
        - Buy 35 gal @ $2.50 = $87.50.
        - Total fuel cost = 17.50 + 87.50 = $105.00.
        """
        s1 = DummyStation("1", "Pricey Stop", "High", "TX", 3.50)
        s2 = DummyStation("2", "Bargain Stop", "Low", "TX", 2.50)
        stations = [(300.0, s1), (550.0, s2)]

        result = plan_fuel_stops(
            total_distance_miles=900.0,
            projected_stations=stations,
            start_name="Origin",
            finish_name="Dest",
        )

        self.assertEqual(len(result.fuel_stops), 2)
        self.assertAlmostEqual(result.fuel_stops[0].gallons_purchased, 5.0, places=2)
        self.assertAlmostEqual(result.fuel_stops[0].cost_usd, 17.50, places=2)
        self.assertAlmostEqual(result.fuel_stops[1].gallons_purchased, 35.0, places=2)
        self.assertAlmostEqual(result.fuel_stops[1].cost_usd, 87.50, places=2)
        self.assertAlmostEqual(result.total_fuel_cost_usd, 105.00, places=2)

    def test_gap_exceeds_500_miles_raises_infeasible_route_error(self):
        """If gap between reachable stations exceeds 500 miles, raise InfeasibleRouteError."""
        s1 = DummyStation("1", "Stop One", "Near", "CO", 3.10)
        s2 = DummyStation("2", "Stop Two", "Far", "TX", 3.20)
        # Gap between s1 (200 mi) and s2 (750 mi) is 550 miles (> 500 miles)
        stations = [(200.0, s1), (750.0, s2)]

        with self.assertRaises(InfeasibleRouteError) as ctx:
            plan_fuel_stops(
                total_distance_miles=900.0,
                projected_stations=stations,
                start_name="Denver, CO",
                finish_name="Dallas, TX",
            )

        err_msg = str(ctx.exception)
        self.assertIn("Route not feasible", err_msg)
        self.assertIn("550.0", err_msg)
        self.assertIn("exceeds 500-mile range", err_msg)


class RouteApiIntegrationTests(TestCase):
    """Integration tests for the /api/route/ endpoint mocking external HTTP."""

    def setUp(self):
        self.client = APIClient()
        clear_geocode_cache()
        clear_cache()

    @patch("requests.get")
    def test_happy_path_route_success(self, mock_requests_get):
        """Verify successful route plan, schema compliance, and cost integrity."""
        def mock_get(url, *args, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "nominatim" in url:
                params = kwargs.get("params", {})
                q = params.get("q", "")
                if "Denver" in q:
                    resp.json.return_value = [{"lat": "39.7392", "lon": "-104.9848"}]
                else:
                    resp.json.return_value = [{"lat": "32.7762", "lon": "-96.7968"}]
            elif "project-osrm" in url:
                # ~780 miles in meters = 1,255,288 meters
                resp.json.return_value = {
                    "code": "Ok",
                    "routes": [
                        {
                            "distance": 1255288.0,
                            "duration": 43200.0,
                            "geometry": {
                                "coordinates": CORRIDOR_POLYLINE
                            },
                        }
                    ],
                }
            return resp

        mock_requests_get.side_effect = mock_get

        payload = {
            "start_location": "Denver, CO",
            "finish_location": "Dallas, TX",
        }
        response = self.client.post("/api/route/", data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()

        self.assertEqual(data["start_location"], "Denver, CO")
        self.assertEqual(data["finish_location"], "Dallas, TX")
        self.assertIn("total_distance_miles", data)
        self.assertIn("total_fuel_cost_usd", data)
        self.assertIn("gallons_used", data)
        self.assertIn("fuel_stops", data)
        self.assertIn("route_geometry", data)
        self.assertEqual(data["route_geometry"]["type"], "LineString")
        self.assertGreater(len(data["fuel_stops"]), 0)

        # Verify cost integrity: sum of stop costs == total_fuel_cost_usd
        sum_stop_costs = round(sum(stop["cost_usd"] for stop in data["fuel_stops"]), 2)
        self.assertEqual(data["total_fuel_cost_usd"], sum_stop_costs)

    @patch("requests.get")
    def test_infeasible_route_returns_422(self, mock_requests_get):
        """An impossible route with a gap > 500 miles returns HTTP 422 with required schema."""
        def mock_get(url, *args, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "nominatim" in url:
                params = kwargs.get("params", {})
                q = params.get("q", "")
                if "Denver" in q:
                    resp.json.return_value = [{"lat": "39.7392", "lon": "-104.9848"}]
                else:
                    resp.json.return_value = [{"lat": "64.2008", "lon": "-149.4937"}]
            elif "project-osrm" in url:
                resp.json.return_value = {
                    "code": "Ok",
                    "routes": [
                        {
                            "distance": 3218688.0,  # ~2000 miles
                            "duration": 100000.0,
                            "geometry": {
                                "coordinates": [
                                    [-104.9848, 39.7392],
                                    [-149.4937, 64.2008],
                                ]
                            },
                        }
                    ],
                }
            return resp

        mock_requests_get.side_effect = mock_get

        payload = {
            "start_location": "Denver, CO",
            "finish_location": "Fairbanks, AK",
        }
        response = self.client.post("/api/route/", data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        data = response.json()
        self.assertIn("error", data)
        self.assertTrue(
            data["error"].startswith("Route not feasible: gap of")
            or "exceeds 500-mile range" in data["error"]
        )

    def test_missing_fields_returns_400(self):
        """Missing start or finish location returns HTTP 400 with field errors."""
        response = self.client.post("/api/route/", data={"start_location": "Denver, CO"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("finish_location", response.json())

    def test_empty_blank_fields_returns_400(self):
        """Blank string values return HTTP 400 validation error."""
        response = self.client.post(
            "/api/route/",
            data={"start_location": "   ", "finish_location": ""},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        data = response.json()
        self.assertIn("start_location", data)
        self.assertIn("finish_location", data)

    @patch("requests.get")
    def test_unknown_location_returns_400(self, mock_requests_get):
        """If Nominatim returns no results, return clean 400."""
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = []
        mock_requests_get.return_value = resp

        response = self.client.post(
            "/api/route/",
            data={"start_location": "NonExistentPlaceXYZ, QQ", "finish_location": "Dallas, TX"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", response.json())

    @patch("requests.get")
    def test_upstream_timeout_returns_502(self, mock_requests_get):
        """If Nominatim or OSRM times out, return clean HTTP 502 with JSON error."""
        mock_requests_get.side_effect = requests.Timeout("Upstream connection timed out")

        response = self.client.post(
            "/api/route/",
            data={"start_location": "Denver, CO", "finish_location": "Dallas, TX"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        data = response.json()
        self.assertIn("error", data)
        self.assertIn("timed out", data["error"].lower())

    @patch("requests.get")
    def test_http_call_budget(self, mock_requests_get):
        """Assert outbound HTTP calls per request: <= 3 on cold cache, 1 on warm cache."""
        clear_geocode_cache()

        def mock_get(url, *args, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "nominatim" in url:
                params = kwargs.get("params", {})
                q = params.get("q", "")
                if "Denver" in q:
                    resp.json.return_value = [{"lat": "39.7392", "lon": "-104.9848"}]
                else:
                    resp.json.return_value = [{"lat": "32.7762", "lon": "-96.7968"}]
            elif "project-osrm" in url:
                resp.json.return_value = {
                    "code": "Ok",
                    "routes": [
                        {
                            "distance": 1255288.0,
                            "duration": 43200.0,
                            "geometry": {
                                "coordinates": CORRIDOR_POLYLINE
                            },
                        }
                    ],
                }
            return resp

        mock_requests_get.side_effect = mock_get

        payload = {"start_location": "Denver, CO", "finish_location": "Dallas, TX"}

        # First request (Cold cache)
        mock_requests_get.reset_mock()
        res1 = self.client.post("/api/route/", data=payload, format="json")
        self.assertEqual(res1.status_code, status.HTTP_200_OK)

        cold_calls = mock_requests_get.call_count
        # At most 3: 2 geocode + 1 OSRM
        self.assertLessEqual(cold_calls, 3)

        # Second request (Warm cache)
        mock_requests_get.reset_mock()
        res2 = self.client.post("/api/route/", data=payload, format="json")
        self.assertEqual(res2.status_code, status.HTTP_200_OK)

        warm_calls = mock_requests_get.call_count
        # Exactly 1: 0 geocode (cache hit) + 1 OSRM
        self.assertEqual(warm_calls, 1)
