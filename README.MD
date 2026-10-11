# Spotter Fuel Route Optimizer — Backend Django API

A high-performance Django REST API and interactive dashboard that calculates cost-optimal fuel stops along any driving route across the USA. Built for the Spotter Backend Django Engineer Assessment.

---

## 1. Quick Start & Setup

### Prerequisites
- Python 3.10+ (tested on Python 3.14)
- Git & Virtualenv

### Installation

```bash
# 1. Navigate into project directory
cd spotter-fuel-api

# 2. Create and activate a clean virtual environment
python -m venv venv
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

# 3. Install pinned dependencies
pip install -r requirements.txt

# 4. Run database migrations
python manage.py migrate

# 5. Start the local development server
python manage.py runserver
```

Open your browser to:
- **Interactive UI Dashboard**: [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- **API Endpoint**: `POST http://127.0.0.1:8000/api/route/`

---

## 2. Running Geocoding (`geocode_stations`)

The assessment dataset `fuel-prices-for-be-assessment.csv` contains 8,151 stations with City/State pairs (no coordinates). The management command `geocode_stations` geocodes unique City/State pairs via Nominatim and persists coordinates incrementally to `routes/data/station_coords.json`.

### Features
- **Resumable**: Automatically skips pairs already present in `station_coords.json`.
- **Rate-Limited**: Strictly obeys OpenStreetMap's Nominatim policy (1 second delay between calls).
- **User-Agent**: Sends `User-Agent: spotter-fuel-api-assessment`.
- **Incremental & Atomic**: Writes to disk after every successful geocode.

### Running a Sample Test Run (e.g. 20 pairs)
```bash
python manage.py geocode_stations --limit 20
```

### Running the Full Job
> [!NOTE]
> Since there are 3,893 unique City/State pairs, running the full job from scratch takes ~1 hour due to the 1-req/sec rate limit. The command is fully interruptible (`Ctrl+C`) and resumable.
```bash
python manage.py geocode_stations
```

---

## 3. Running the Test Suite

The test suite in `routes/tests.py` covers:
- Pure Python unit tests for the optimal fuel planner with hand-calculated expected costs
- Infeasible route detection (> 500-mile gap returns HTTP 422 with exact required schema)
- Missing and blank field validation (returns HTTP 400)
- Upstream network timeouts (returns HTTP 502 Bad Gateway)
- HTTP call budget verification: **≤ 3 outbound HTTP calls on cold cache, exactly 1 on warm cache**

Run tests via:
```bash
python manage.py test
```

Expected output:
```
Ran 11 tests in 0.119s

OK
```

---

## 4. API Specification & Example

### Endpoint
`POST /api/route/`  
`Content-Type: application/json`

### Request Body
```json
{
  "start_location": "Denver, CO",
  "finish_location": "Dallas, TX"
}
```

### Example Curl Request
```bash
curl -X POST http://127.0.0.1:8000/api/route/ \
  -H "Content-Type: application/json" \
  -d '{"start_location": "Denver, CO", "finish_location": "Dallas, TX"}'
```

### Live Example Response
```json
{
  "start_location": "Denver, CO",
  "finish_location": "Dallas, TX",
  "total_distance_miles": 795.65,
  "total_fuel_cost_usd": 82.94,
  "gallons_used": 79.56,
  "fuel_stops": [
    {
      "stop_number": 1,
      "truckstop_name": "QUIKTRIP #7914",
      "address": "I-27 EXIT 117",
      "city": "Amarillo",
      "state": "TX",
      "retail_price_per_gallon": 2.846,
      "gallons_purchased": 15.59,
      "cost_usd": 44.35,
      "latitude": 35.20729,
      "longitude": -101.837119,
      "distance_from_start_miles": 428.3
    },
    {
      "stop_number": 2,
      "truckstop_name": "7-ELEVEN",
      "address": "I-44, EXIT 1/US-287",
      "city": "Wichita Falls",
      "state": "TX",
      "retail_price_per_gallon": 2.761,
      "gallons_purchased": 13.98,
      "cost_usd": 38.59,
      "latitude": 33.900457,
      "longitude": -98.502078,
      "distance_from_start_miles": 655.9
    }
  ],
  "route_geometry": {
    "type": "LineString",
    "coordinates": [
      [-104.9848, 39.7392],
      ...
    ]
  }
}
```

### Error Responses
- **400 Bad Request**: Missing or blank fields, or unrecognized location.
  ```json
  { "start_location": ["This field may not be blank."] }
  ```
- **422 Unprocessable Entity**: Infeasible route where distance gap exceeds 500-mile vehicle range.
  ```json
  { "error": "Route not feasible: gap of 650.0 miles exceeds 500-mile range between Denver, CO and Omaha, NE" }
  ```
- **502 Bad Gateway**: Upstream OSRM or Nominatim timeout or failure.
  ```json
  { "error": "OSRM routing service unavailable or timed out" }
  ```

---

## 5. Design Notes

### Why Exactly 1 OSRM Call Per Request
Calling map or routing APIs per fuel station would require thousands of outbound HTTP calls, introducing massive latency (10+ seconds) and exceeding rate limits. Instead, we query OSRM **once** between the start and finish coordinates, obtaining the full polyline geometry. Candidate stations are then mathematically projected onto the polyline locally using equirectangular Cartesian projection and Haversine distance, keeping computational latency under **50 milliseconds**.

### Caching Architecture
1. **Module-Level Station Cache**: CSV parsing occurs once on application startup.
2. **Persistent Coordinate Cache (`station_coords.json`)**: Geocoded city/state coordinates are loaded into an in-memory dictionary.
3. **In-Memory Geocoding Cache**: Repeated geocoding queries within the process lifetime require 0 external HTTP calls.
4. **Call Budget**: Cold cache queries use at most 3 HTTP calls (2 Nominatim + 1 OSRM). Warm cache queries execute in **1 single HTTP call** (OSRM only).

### Optimal Fuel Stopping Algorithm
- **Vehicle Constraints**: 500-mile range on full tank, 10 MPG (50-gallon tank capacity). Starts at origin with a full tank.
- **Candidate Filtering**: Bounding-box prefilter discards stations outside the route corridor. Point-to-segment projection retains stations within `MAX_DETOUR_MILES = 10` miles and calculates cumulative mileage from origin.
- **Greedy Cheapest-Reachable Selection**:
  1. If the destination is within current reachable range, proceed directly to destination (0 stops).
  2. Otherwise, inspect all stations within reachable fuel range.
  3. Select the cheapest reachable station.
  4. At the stop:
     - If a cheaper station exists ahead within full tank reach, purchase only enough fuel to reach that cheaper station.
     - If no cheaper station exists ahead and the destination is within full tank reach, purchase only enough fuel to reach destination.
     - Otherwise, fill the tank to capacity (50 gallons).
  5. If the gap to the next available station exceeds 500 miles, raise `InfeasibleRouteError` (HTTP 422).

---

## 6. Assumptions & Deviations

1. **Station Location Granularity**: The CSV provides `City` and `State`, but not street-level latitude/longitude. Coordinates represent the city/town centroid. Stations located within the city limits are projected onto nearby highways within 10 miles.
2. **Fuel Left on Arrival**: The driver is not required to arrive at the destination with a full tank; rather, the driver only buys what is needed to reach the destination once within range of the cheapest final station.
3. **Duplicate OPIS IDs**: The dataset contains 678 stations with multiple rows (e.g. rack variations or minor brand differences). When multiple stations share the same location along the route, the planner prioritizes the lowest retail price.