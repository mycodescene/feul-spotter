Spotter Backend Django Engineer — Coding Assessment
Complete Build Instructions for Antigravity
Goal: Build a Django REST API that plans a USA road trip route with cost-optimal fuel stops. This is a take-home assessment for Spotter's Backend Django Engineer role. Follow every step below exactly.

1. Project Setup
# Create project directory
mkdir spotter-fuel-api && cd spotter-fuel-api

# Python 3.11+ recommended
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install django djangorestframework requests
pip freeze > requirements.txt
django-admin startproject config .
python manage.py startapp routes
settings.py — add to INSTALLED_APPS:
INSTALLED_APPS = [
    ...
    'rest_framework',
    'routes',
]
Data file
Place fuel-prices-for-be-assessment.csv at routes/data/fuel-prices-for-be-assessment.csv (create the data/ folder). The CSV has 8,151 rows with columns:
OPIS Truckstop ID, Truckstop Name, Address, City, State, Rack ID, Retail Price

2. Free Map / Routing API Choice
Use OSRM (Open Source Routing Machine) — public demo server, no API key needed:
Route: https://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=full&geometries=geojson&steps=false
Geocoding (city/state → lat/lon): use Nominatim (OpenStreetMap):
  https://nominatim.openstreetmap.org/search?q={city},{state},USA&format=json&limit=1
  Header required: User-Agent: spotter-fuel-api-assessment
Why OSRM: no signup, no key, generous for a demo. One route call per request satisfies the "1 call is ideal" requirement.
Fallback: if OSRM is down, use OpenRouteService free tier (needs free API key). Keep the routing client behind an interface so the provider is swappable.

3. API Design
Endpoint
POST /api/route/
Content-Type: application/json
Request body
{
  "start_location": "Denver, CO",
  "finish_location": "Dallas, TX"
}
Both locations are city/state strings within the USA.
Response body
{
  "start_location": "Denver, CO",
  "finish_location": "Dallas, TX",
  "total_distance_miles": 780.4,
  "total_fuel_cost_usd": 234.12,
  "gallons_used": 78.04,
  "fuel_stops": [
    {
      "stop_number": 1,
      "truckstop_name": "CIRCLE K #2612042",
      "address": "I-35, EXIT 271",
      "city": "Jarrell",
      "state": "TX",
      "retail_price_per_gallon": 2.919,
      "gallons_purchased": 50.0,
      "cost_usd": 145.95,
      "latitude": 30.82,
      "longitude": -97.60,
      "distance_from_start_miles": 620.5
    }
  ],
  "route_geometry": { "type": "LineString", "coordinates": [[-104.99, 39.74], ...] }
}

4. Core Algorithm — Optimal Fuel Stops
Vehicle constants:
MAX_RANGE_MILES = 500 (full tank range)
MPG = 10 (miles per gallon → tank = 50 gallons)
Steps:
Geocode start and finish via Nominatim → (lat, lon) each. Cache results in-memory (dict) so repeated city lookups don't re-hit the API.
Route via OSRM (ONE call) → total distance (meters → miles), full GeoJSON geometry (list of [lon, lat] coordinates).
Load fuel stations from CSV once at startup (module-level cache). For each station, geocode is NOT available in the CSV — only City/State. Strategy:
Geocode each unique City/State pair via Nominatim once, cache to a local JSON file (routes/data/station_coords.json) so subsequent runs need zero geocoding calls. 8,151 rows but far fewer unique city/state pairs (~a few thousand). Do this as a one-time management command: python manage.py geocode_stations.
If a station's city can't be geocoded, skip it (log it).
Project stations onto the route: for each geocoded station, compute distance from the route polyline (point-to-segment distance over the GeoJSON coordinates). Keep stations within X miles of the route (e.g. 10 miles — make it a constant MAX_DETOUR_MILES = 10). Sort kept stations by distance-along-route (projected position).
Greedy cheapest-reachable algorithm (classic gas-station problem):
Start with a full tank (500 mi range).
At each point, look ahead at all stations within current reachable range; drive to the cheapest station within reach (not the furthest — cheapest, since prices vary a lot: $2.69–$6.40).
Buy only enough fuel to reach the next cheaper station within range, or fill up if none cheaper ahead (standard optimal strategy). Simplified acceptable version: at each stop, fill enough to reach the cheapest reachable station ahead, else fill the tank.
Edge case: if a gap between consecutive reachable stations exceeds 500 miles, return a 422 error: {"error": "Route not feasible: gap of X miles exceeds 500-mile range between ... and ..."}.
Cost: gallons = distance_miles / 10; cost = gallons × retail_price. Round money to 2 decimals.
Performance: the whole computation (excluding the 1 OSRM + up to 2 Nominatim calls) must run in well under a second. Precompute station coords; use efficient point-to-segment math (no per-station API calls at request time).

5. Code Layout
spotter-fuel-api/
├── config/
│   ├── settings.py
│   └── urls.py            # include routes.urls
├── routes/
│   ├── data/
│   │   ├── fuel-prices-for-be-assessment.csv
│   │   └── station_coords.json   # generated by management command
│   ├── management/commands/
│   │   └── geocode_stations.py   # one-time: geocode unique City/State pairs
│   ├── serializers.py     # request/response validation
│   ├── services/
│   │   ├── geocoding.py   # Nominatim client + cache
│   │   ├── routing.py     # OSRM client
│   │   ├── stations.py    # CSV loading + coord cache
│   │   └── planner.py     # fuel-stop optimization algorithm
│   ├── views.py           # POST /api/route/
│   ├── urls.py
│   └── tests.py           # at least: happy path, infeasible route, bad input
├── requirements.txt
└── README.md              # how to run
Key implementation notes:
planner.py must be pure logic (no Django imports) — easy to unit test.
Validate inputs with DRF serializers: both locations required, non-empty strings.
Handle upstream failures gracefully: Nominatim/OSRM timeout → 502 with clear message. Set timeout=10 on all requests calls.
Respect Nominatim usage policy: 1 request/second max → time.sleep(1) between geocode calls in the management command.
Add User-Agent header to every Nominatim request (required by their policy).

6. What NOT to do
Do NOT call the map/routing API per station (that would be thousands of calls).
Do NOT hardcode API keys anywhere.
Do NOT use paid APIs.
Do NOT return HTML — JSON only.
Do NOT skip input validation.

7. Testing Checklist (run before calling it done)
[ ] python manage.py test passes
[ ] POST Denver, CO → Dallas, TX returns fuel stops, total cost, route geometry
[ ] POST with an impossible leg (e.g. Honolulu, HI → Miami, FL — tests long-gap handling) returns clean 422, not a 500
[ ] POST with empty/missing fields returns 400 with field errors
[ ] Count outbound HTTP calls for one request: ≤ 3 (2 geocode + 1 route on cold cache; 1 on warm cache)
[ ] Response time < 2s on warm cache
[ ] pip freeze matches requirements.txt; fresh venv install works

8. Deliverables for the submission
GitHub repo — public or shareable private link, with this README + run instructions.
Loom video (≤ 5 min) — script:
0:00–0:30 — what the API does
0:30–2:30 — Postman demo: live POST, walk through the JSON response
2:30–4:30 — code walkthrough: planner algorithm, why 1 OSRM call, caching design
4:30–5:00 — tests + how to run
Submit both links in the Spotter questionnaire: https://careers.spotter.ai/questionnaires/23cd0847-66b1-4e5a-9cf0-43ff36d93a0b/63167021-20fd-4635-8684-a79f75f00baa
Deadline: within 3 days of receiving the exercise (received Oct 11 → submit by Oct 14).
$100 bonus on successful completion.
