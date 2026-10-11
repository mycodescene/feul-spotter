"""Views for route fuel planning API and interactive dashboard.

Strict JSON error responses:
- 400: Validation or location lookup failures
- 422: Infeasible route (gap > 500 mi or disconnected path)
- 502: Upstream service (OSRM / Nominatim) timeout or failure
"""

from __future__ import annotations

import logging
from django.shortcuts import render
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import RouteRequestSerializer
from .services.geocoding import (
    GeocodingError,
    UpstreamServiceError,
    geocode_location,
)
from .services.planner import (
    InfeasibleRouteError,
    plan_fuel_stops,
    project_stations_onto_route,
)
from .services.routing import RoutingError, get_route
from .services.stations import get_geocoded_stations

logger = logging.getLogger(__name__)


class RoutePlanView(APIView):
    """Plans cost-optimal fuel stops along a driving route between two US locations."""

    def post(self, request, *args, **kwargs) -> Response:
        serializer = RouteRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        start_loc = serializer.validated_data["start_location"]
        finish_loc = serializer.validated_data["finish_location"]

        # 1. Geocode start and finish locations (using in-memory cache)
        try:
            start_coords = geocode_location(start_loc)
        except GeocodingError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except UpstreamServiceError as e:
            return Response({"error": str(e)}, status=status.HTTP_502_BAD_GATEWAY)

        try:
            finish_coords = geocode_location(finish_loc)
        except GeocodingError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except UpstreamServiceError as e:
            return Response({"error": str(e)}, status=status.HTTP_502_BAD_GATEWAY)

        # 2. Query driving route via OSRM (strictly 1 call per request)
        try:
            route_res = get_route(start_coords, finish_coords)
        except RoutingError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        except UpstreamServiceError as e:
            return Response({"error": str(e)}, status=status.HTTP_502_BAD_GATEWAY)
        except Exception as e:
            logger.error(f"Unexpected routing failure: {e}", exc_info=True)
            return Response(
                {"error": f"Upstream routing failure: {e}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # 3. Filter and project stations along route polyline
        geocoded_stations = get_geocoded_stations()
        projected_stations = project_stations_onto_route(
            coordinates=route_res.coordinates,
            stations=geocoded_stations,
            total_route_distance_miles=route_res.total_distance_miles,
        )

        # 4. Plan optimal fuel stops using greedy cheapest-reachable strategy
        try:
            fuel_plan = plan_fuel_stops(
                total_distance_miles=route_res.total_distance_miles,
                projected_stations=projected_stations,
                start_name=start_loc,
                finish_name=finish_loc,
            )
        except InfeasibleRouteError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        except Exception as e:
            logger.error(f"Error computing fuel plan: {e}", exc_info=True)
            return Response(
                {"error": f"Computation error: {e}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # 5. Build response matching API schema
        response_data = {
            "start_location": start_loc,
            "finish_location": finish_loc,
            "total_distance_miles": fuel_plan.total_distance_miles,
            "total_fuel_cost_usd": fuel_plan.total_fuel_cost_usd,
            "gallons_used": fuel_plan.gallons_used,
            "fuel_stops": [stop.to_dict() for stop in fuel_plan.fuel_stops],
            "route_geometry": route_res.geojson,
        }

        return Response(response_data, status=status.HTTP_200_OK)


def dashboard_view(request):
    """Render interactive frontend dashboard."""
    return render(request, "routes/index.html")
