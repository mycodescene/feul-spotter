"""Serializers for route requests and responses.

Enforces strict input validation for start_location and finish_location.
"""

from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    """Validates the input parameters for route planning."""

    start_location = serializers.CharField(
        required=True,
        allow_blank=False,
        trim_whitespace=True,
        max_length=255,
        help_text="Starting city and state in the USA (e.g. 'Denver, CO')",
    )
    finish_location = serializers.CharField(
        required=True,
        allow_blank=False,
        trim_whitespace=True,
        max_length=255,
        help_text="Destination city and state in the USA (e.g. 'Dallas, TX')",
    )

    def validate_start_location(self, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise serializers.ValidationError("Start location cannot be empty.")
        return clean

    def validate_finish_location(self, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise serializers.ValidationError("Finish location cannot be empty.")
        return clean


class FuelStopSerializer(serializers.Serializer):
    """Serializes individual fuel stop details."""

    stop_number = serializers.IntegerField()
    truckstop_name = serializers.CharField()
    address = serializers.CharField()
    city = serializers.CharField()
    state = serializers.CharField()
    retail_price_per_gallon = serializers.FloatField()
    gallons_purchased = serializers.FloatField()
    cost_usd = serializers.FloatField()
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    distance_from_start_miles = serializers.FloatField()


class RouteResponseSerializer(serializers.Serializer):
    """Serializes the complete trip optimization response."""

    start_location = serializers.CharField()
    finish_location = serializers.CharField()
    total_distance_miles = serializers.FloatField()
    total_fuel_cost_usd = serializers.FloatField()
    gallons_used = serializers.FloatField()
    fuel_stops = FuelStopSerializer(many=True)
    route_geometry = serializers.DictField()
