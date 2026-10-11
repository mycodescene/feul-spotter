"""Django management command to geocode unique City/State pairs from fuel prices CSV.

Resumable, rate-limited (1s delay per Nominatim policy), and writes incrementally
to routes/data/station_coords.json.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Dict, Tuple
from django.core.management.base import BaseCommand
import requests

from routes.services.stations import (
    COORDS_PATH,
    DATA_DIR,
    get_unique_city_states_from_csv,
    load_station_coordinates,
    normalize_city_state,
    save_station_coordinates,
)

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "spotter-fuel-api-assessment"
RATE_LIMIT_DELAY = 1.0  # Respect Nominatim 1 req/sec policy
NON_US_STATES = {"AB", "BC", "MB", "NB", "NS", "ON", "QC", "SK", "YT"}


class Command(BaseCommand):
    help = "Geocode unique City/State pairs from CSV using Nominatim and cache to JSON."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maximum number of new city/state pairs to geocode in this run (e.g. 20 for testing).",
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=RATE_LIMIT_DELAY,
            help=f"Delay between requests in seconds (default: {RATE_LIMIT_DELAY}s).",
        )

    def handle(self, *args, **options):
        limit = options.get("limit")
        delay = options.get("delay")

        self.stdout.write(self.style.NOTICE("Loading existing coordinate cache..."))
        existing_coords = load_station_coordinates(force_reload=True)
        self.stdout.write(
            self.style.SUCCESS(f"Found {len(existing_coords)} existing cached city/state coordinates.")
        )

        all_pairs = get_unique_city_states_from_csv()
        self.stdout.write(f"Total unique City/State pairs in CSV: {len(all_pairs)}")

        # Find pairs needing geocoding (skip non-US states since assessment is USA-only)
        pending_pairs = [
            (c, s)
            for c, s in all_pairs
            if s.strip().upper() not in NON_US_STATES
            and normalize_city_state(c, s) not in existing_coords
        ]

        self.stdout.write(f"Pairs remaining to geocode: {len(pending_pairs)}")
        if not pending_pairs:
            self.stdout.write(self.style.SUCCESS("All city/state pairs already geocoded!"))
            return

        if limit is not None and limit > 0:
            self.stdout.write(self.style.WARNING(f"Limiting this run to {limit} pairs."))
            pending_pairs = pending_pairs[:limit]

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }

        successful = 0
        failed = 0
        coords_map = dict(existing_coords)

        try:
            for idx, (city, state) in enumerate(pending_pairs, start=1):
                state_upper = state.strip().upper()
                if state_upper in NON_US_STATES:
                    continue

                norm_key = normalize_city_state(city, state)
                query = f"{city}, {state}, USA"
                self.stdout.write(f"[{idx}/{len(pending_pairs)}] Geocoding '{query}'...", ending="")

                try:
                    resp = requests.get(
                        NOMINATIM_URL,
                        params={"q": query, "format": "json", "limit": 1},
                        headers=headers,
                        timeout=10,
                    )

                    if resp.status_code == 200:
                        data = resp.json()
                        if data and isinstance(data, list) and len(data) > 0:
                            lat = float(data[0]["lat"])
                            lon = float(data[0]["lon"])
                            coords_map[norm_key] = (lat, lon)
                            save_station_coordinates(coords_map)
                            successful += 1
                            self.stdout.write(self.style.SUCCESS(f" OK -> ({lat:.4f}, {lon:.4f})"))
                        else:
                            failed += 1
                            self.stdout.write(self.style.WARNING(" NOT FOUND (skipping)"))
                    else:
                        failed += 1
                        self.stdout.write(self.style.ERROR(f" HTTP {resp.status_code}"))

                except Exception as e:
                    failed += 1
                    self.stdout.write(self.style.ERROR(f" ERROR: {e}"))

                # Respect rate limiting
                time.sleep(delay)

        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("\nInterrupted by user. Progress has been saved."))

        self.stdout.write(
            self.style.SUCCESS(
                f"\nGeocoding run completed. Newly added: {successful}, Failed/Skipped: {failed}. "
                f"Total cached: {len(coords_map)}"
            )
        )
