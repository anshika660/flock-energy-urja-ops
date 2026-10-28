# Urja Meter Ops — Modern REST API Gateway & Fleet Intelligence

A production-ready, high-performance Python FastAPI service built in front of the legacy **Urja Meter Ops** portal (`https://urja-ops.flockenergy.tech`).

This service replaces manual copy-pasting from a browser with a clean, documented, and resilient REST API. It resolves legacy schema discrepancies, reconstructs and self-heals corrupted network hierarchies, provides sub-millisecond multi-attribute and geospatial proximity querying, analyzes interval consumption readings, and serves an interactive web operations dashboard.

---

## Table of Contents
1. [Key Features & Highlights](#key-features--highlights)
2. [Project Architecture & Structure](#project-architecture--structure)
3. [Quick Start & Installation](#quick-start--installation)
4. [Sample Requests & Output](#sample-requests--output)
5. [System Design Decisions & Trade-Offs](#system-design-decisions--trade-offs)
6. [Scale Analysis (Where this approach struggles & what changes)](#scale-analysis)
7. [What was Intentionally Skipped](#what-was-intentionally-skipped)
8. [What I'd Improve with More Time](#what-id-improve-with-more-time)
9. [Related Documentation Links](#related-documentation-links)
10. [Automated Test Suite](#automated-test-suite)

---

## Key Features & Highlights

- **Clean RESTful API with OpenAPI 3.1 & Swagger UI**: Strictly typed Pydantic v2 data models with interactive documentation available out of the box at `/docs`.
- **Reverse-Engineered Bulk Ingestion via Signed HMAC-SHA256**: Uncovered and reverse-engineered the portal's internal bulk export endpoint (`/portal/export`), unlocking the complete 403-meter dataset with single-call synchronization rather than 20-item paging.
- **Self-Healing Electrical Network Hierarchy**: Reconstructs the complete topology (`Discom -> Zone -> Circle -> Division -> Subdivision -> Substation -> Feeder -> DT -> Meters`). Automatically detects and heals 22 corrupted records containing missing codes or names.
- **Local Index & Multi-Attribute Query Engine**: Overcomes the legacy portal's limitation (which only searches meter ID/serial) by enabling rich filtering by manufacturer (`HPL`, `L&T`, `Genus`, `Secure`, `Allied`), phase (`single`, `three`), status (`Installed`, `Faulty`, `Decommissioned`), build (`legacy`, `v2`), transformer code, feeder code, and sorting.
- **Geospatial Proximity Search (`/api/v1/meters/nearby`)**: High-speed Haversine distance calculation finding meters within radius $R$ km of any coordinate.
- **Consumption Analytics**: Standardizes 337 half-hourly interval readings over 7 days into numeric floats and ISO timestamps, computing net active energy, voltage bounds, and estimated power factor.
- **Resilient Upstream Session Management**: Automatic SvelteKit CSRF origin handling, session cookie tracking, and transparent automatic re-authentication upon token expiration.
- **Modern Web Client Dashboard**: Hosted directly at `/` and `/dashboard` featuring stat summary cards, an interactive Leaflet geospatial map with color-coded pins, a collapsible hierarchy explorer, and Chart.js 7-day consumption and voltage charts.

---

## Project Architecture & Structure

```
.
├── app/
│   ├── __init__.py
│   ├── config.py           # Typed environment configuration via pydantic-settings
│   ├── client.py           # UrjaPortalClient: SvelteKit auth, HMAC signing, retries
│   ├── service.py          # MeterService: self-healing hierarchy, in-memory index, geo queries
│   ├── schemas.py          # Pydantic v2 models (requests, responses, pagination, analytics)
│   ├── main.py             # FastAPI entrypoint, CORS, lifespan startup cache pre-warming
│   ├── api/
│   │   ├── deps.py         # Service dependency injection
│   │   ├── meters.py       # /api/v1/meters (list, filter, nearby, detail, energy, geo)
│   │   ├── hierarchy.py    # /api/v1/hierarchy (tree, summary, transformers, DT detail)
│   │   ├── analytics.py    # /api/v1/analytics (fleet metrics, status/make breakdowns)
│   │   └── system.py       # /health and /api/v1/cache/refresh
│   └── static/
│       └── index.html      # Modern Web Client (Tailwind CSS + Leaflet map + Chart.js)
├── tests/
│   ├── test_service.py     # Unit tests: Haversine distance, timestamp parsing, schemas
│   └── test_api.py         # Integration tests: FastAPI endpoints with Starlette TestClient
├── openapi.json            # Exported OpenAPI 3.1 specification
├── PROTOCOL.md             # Reverse-engineering documentation of the Urja ops portal
├── REFLECTION.md           # Engineering reflections & answers to evaluation questions
├── requirements.txt        # Pinned dependencies
├── .env.example            # Environment variables template
└── README.md               # Main project front door
```

---

## Quick Start & Installation

### 1. Prerequisites
- Python 3.10+ (tested with Python 3.11)
- Git

### 2. Setup Virtual Environment & Install Dependencies
```bash
# Clone the repository
git clone https://github.com/your-username/flock-energy-takehome.git
cd flock-energy-takehome

# Create and activate virtual environment
python -m venv venv
# On Windows:
.\venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate

# Install required packages
pip install -r requirements.txt
```

### 3. Configure Environment Variables
A pre-configured `.env` is provided. If configuring from scratch:
```bash
cp .env.example .env
```
Default parameters in `.env`:
```ini
URJA_PORTAL_URL=https://urja-ops.flockenergy.tech
URJA_USERNAME=operator@urja.local
URJA_PASSWORD=urja-ops-2026
PORT=8000
HOST=0.0.0.0
VERIFY_SSL=false
CACHE_TTL_SECONDS=300
```

### 4. Run the Service
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
On startup, the service pre-warms its cache by authenticating with the portal, ingesting all 403 meters, healing corrupted hierarchy records, and generating a fresh `openapi.json`.

- **Web Dashboard**: [http://localhost:8000/](http://localhost:8000/)
- **Interactive Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc Documentation**: [http://localhost:8000/redoc](http://localhost:8000/redoc)
- **Raw OpenAPI Spec**: [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json)

---

## Sample Requests & Output

### 1. Multi-Attribute Meter Filtering
Filter meters manufactured by **HPL** that are **Installed**, paginated by 2:

```bash
curl -X GET "http://localhost:8000/api/v1/meters?make=HPL&install_status=Installed&page=1&limit=2"
```

**Response (`200 OK`)**:
```json
{
  "data": [
    {
      "meter_id": "J100015",
      "serial_no": "HP30855",
      "make": "HPL",
      "phase_type": "single",
      "install_status": "Installed",
      "install_type": "Whole Current",
      "build": "legacy",
      "dt_code": "DT-016",
      "feeder_code": "F-016",
      "geo": {
        "latitude": 26.963479,
        "longitude": 75.839841
      }
    },
    {
      "meter_id": "J100075",
      "serial_no": "HP89617",
      "make": "HPL",
      "phase_type": "three",
      "install_status": "Installed",
      "install_type": "Whole Current",
      "build": "v2",
      "dt_code": "DT-036",
      "feeder_code": "F-016",
      "geo": {
        "latitude": 26.920257,
        "longitude": 75.828624
      }
    }
  ],
  "total": 46,
  "page": 1,
  "limit": 2,
  "total_pages": 23
}
```

---

### 2. Geospatial Proximity Search
Find meters within **2 km** of central Jaipur (`lat=26.9124, lng=75.7873`):

```bash
curl -X GET "http://localhost:8000/api/v1/meters/nearby?lat=26.9124&lng=75.7873&radius_km=2.0&limit=2"
```

**Response (`200 OK`)**:
```json
[
  {
    "meter_id": "J100159",
    "serial_no": "AL77148",
    "make": "Allied",
    "phase_type": "three",
    "install_status": "Installed",
    "install_type": "Whole Current",
    "build": "legacy",
    "dt_code": "DT-040",
    "feeder_code": "F-020",
    "geo": {
      "latitude": 26.918903,
      "longitude": 75.792518
    },
    "distance_km": 0.902
  },
  {
    "meter_id": "J100350",
    "serial_no": "SE32545",
    "make": "Secure",
    "phase_type": "single",
    "install_status": "Installed",
    "install_type": "CT Operated",
    "build": "legacy",
    "dt_code": "DT-030",
    "feeder_code": "F-010",
    "geo": {
      "latitude": 26.903144,
      "longitude": 75.782012
    },
    "distance_km": 1.154
  }
]
```

---

### 3. Meter Detail with Healed Hierarchy
Retrieve full detail for meter `J100011` (whose circle code was corrupted upstream):

```bash
curl -X GET "http://localhost:8000/api/v1/meters/J100011"
```

**Response (`200 OK`)**:
```json
{
  "meter_id": "J100011",
  "serial_no": "GE64162",
  "make": "Genus",
  "phase_type": "three",
  "install_status": "Installed",
  "install_type": "CT Operated",
  "build": "legacy",
  "dt_code": "DT-012",
  "feeder_code": "F-012",
  "geo": {
    "latitude": 26.852924,
    "longitude": 75.878144
  },
  "hierarchy": {
    "zone": { "code": "Z-02", "name": "Jaipur Zone 2" },
    "circle": { "code": "C-06", "name": "Circle 6" },
    "division": { "code": "D-02", "name": "Division 2" },
    "subdivision": { "code": "SD-02", "name": "Subdivision 2" },
    "substation": { "code": "SS-12", "name": "Substation 12" },
    "feeder": { "code": "F-012", "name": "Feeder 12" },
    "dt": { "code": "DT-012", "name": "Sanganer DT 12" }
  },
  "extra_parameters": {
    "build": "legacy",
    "installType": "CT Operated"
  }
}
```

---

### 4. 7-Day Energy Consumption Analytics
Retrieve interval telemetry with summary KPIs for meter `J100000`:

```bash
curl -X GET "http://localhost:8000/api/v1/meters/J100000/energy?include_summary=true&limit=2"
```

**Response (`200 OK`)**:
```json
{
  "meter_id": "J100000",
  "summary": {
    "reading_count": 337,
    "first_timestamp": "23/06/2026 23:30",
    "last_timestamp": "30/06/2026 23:30",
    "start_kwh": 48438.74,
    "end_kwh": 48580.79,
    "net_kwh_consumed": 142.05,
    "avg_voltage_r": 229.8,
    "min_voltage_r": 220.0,
    "max_voltage_r": 240.0,
    "estimated_power_factor": 0.926
  },
  "readings": [
    {
      "timestamp": "23/06/2026 23:30",
      "timestamp_iso": "2026-06-23T23:30:00",
      "kwh": 48438.74,
      "kvah": 52313.84,
      "volt_r": 226.0
    },
    {
      "timestamp": "24/06/2026 00:00",
      "timestamp_iso": "2026-06-24T00:00:00",
      "kwh": 48439.16,
      "kvah": 52314.29,
      "volt_r": 229.0
    }
  ]
}
```

---

### 5. Fleet Operational Statistics
```bash
curl -X GET "http://localhost:8000/api/v1/analytics/stats"
```

**Response (`200 OK`)**:
```json
{
  "total_meters": 403,
  "by_status": {
    "Installed": 238,
    "Faulty": 90,
    "Decommissioned": 75
  },
  "by_make": {
    "HPL": 90,
    "L&T": 64,
    "Genus": 88,
    "Allied": 76,
    "Secure": 85
  },
  "by_phase": {
    "single": 277,
    "three": 126
  },
  "by_build": {
    "legacy": 238,
    "v2": 165
  },
  "total_dts": 40,
  "total_feeders": 28,
  "total_substations": 18,
  "total_transformer_capacity_kva": 8601.0
}
```

---

## System Design Decisions & Trade-Offs

| Decision | Chosen Approach | Alternative Considered | Rationale & Trade-off |
|---|---|---|---|
| **Bulk Hydration vs. On-Demand Scraping** | Signed bulk export on background pre-warm | Scrape `/portal/meters/search` on every user request | Querying the portal for every request adds 400ms–1.5s latency and bombards the legacy system. The signed bulk export endpoint fetches all 403 meters in 1 HTTP call. The trade-off is a 5-minute data staleness window, mitigated by a manual `/api/v1/cache/refresh` endpoint. |
| **Hierarchy Self-Healing** | In-memory bidirectional canonical mapping | Leave records blank or hardcode fixes | 22 records had either a missing code or missing name. Because the mapping between named units (e.g. `Circle 6`) and codes (`C-06`) is 1-to-1 across the dataset, dynamic bidirectional inference heals 100% of anomalies dynamically without fragile hardcoded lists. |
| **In-Memory Query Index** | Python native filtering + Haversine | Embedded SQLite / DuckDB | For 403 meters, in-memory structures execute in under 0.5ms with zero external database dependencies. Adding SQLite would introduce disk I/O and migration overhead with no performance gain at this scale. |
| **Web Client Delivery** | Self-contained SPA hosted by FastAPI | Standalone Next.js / React application | Delivering a responsive Tailwind + Leaflet + Chart.js dashboard directly through FastAPI eliminates build-step dependencies (Node/npm), enabling any engineer to run the backend and explore the data with a single `uvicorn` command. |

---

## Scale Analysis

### At What Data Scale Does This Approach Struggle?
The current in-memory model is tailored for the current utility fleet of 403 smart meters:
1. **Memory Ceiling**: At ~2 KB per meter object, 10,000 meters consume ~20 MB RAM (completely fine). At **500,000 to 10,000,000 meters** (typical state DISCOM scale), storing objects in raw Python memory would consume 1–20 GB RAM and risk CPU starvation during garbage collection.
2. **Linear Search ($O(N)$)**: Filtering and Haversine distance calculations evaluate every meter sequentially. For 403 items, this takes **< 1ms**. For 1,000,000 items, calculating Haversine distances on every request would take ~1.5–2.5 seconds of CPU time per query.
3. **Bulk Sync Feasibility**: Ingesting 1,000,000 meters in a single HTTP payload would result in multi-gigabyte JSON payloads, causing network timeouts and memory spikes.

### What Would We Change at Scale?
- **Relational Storage with Spatial Indexing**: Migrate storage to **PostgreSQL + PostGIS**. Geographic queries like `/meters/nearby` would leverage an R-Tree / GiST spatial index (`ST_DWithin`), reducing radius queries from $O(N)$ to $O(\log N)$ (sub-10ms for millions of rows).
- **Time-Series Engine for Readings**: Smart meter telemetry (15-min or 30-min interval readings) scales to billions of rows. We would route consumption data into **TimescaleDB** or **ClickHouse** with continuous aggregate rollups for instantaneous daily/weekly KPI generation.
- **Distributed Cache**: Replace in-process memory with **Redis** so multiple horizontal gateway replicas share a synchronized cache without duplicate upstream fetching.
- **Change-Data Capture (CDC) / Partitioned Polling**: Partition sync jobs by feeder or substation so ingestion occurs incrementally rather than via a monolithic bulk pull.

---

## What was Intentionally Skipped
- **Portal State Mutations (Write Endpoints)**: Ground rules explicitly require treating the portal as read-only. We intentionally built no provisioning or mutation endpoints.
- **User Authentication / RBAC on the Gateway**: To keep integration frictionless for internal product/data teams, the gateway does not mandate API keys or OAuth2 tokens. In enterprise production, an API gateway (e.g. Kong, Envoy) or FastAPI OAuth2 bearer dependencies would be added.
- **Upstream Connection Pooling Daemon**: A persistent daemon polling every 5 seconds was avoided to prevent unnecessary server load on the legacy portal. Instead, TTL cache expiry with reactive refresh on request ensures minimal upstream footprint.

---

## What I'd Improve with More Time
1. **TimescaleDB Telemetry Store**: Ingest historical consumption intervals into TimescaleDB to support long-term anomaly detection (e.g. voltage sags, power theft detection via sudden load drop).
2. **Resilient Dual-Ingestion Pipeline**: If `/portal/export` were deprecated by upstream developers, fallback immediately to concurrent crawling of `/portal/meters/search` and SSR page hydration parsing.
3. **Outage & Anomaly Prediction**: Compute real-time voltage deviation alerts across DT clusters to detect phase imbalance or transformer overload before physical failure occurs.

---

## Related Documentation Links
- **[PROTOCOL.md](file:///c:/Users/hp/OneDrive/Desktop/Flock%20energy%20assignment/PROTOCOL.md)**: Reverse-engineering documentation of the Urja Meter Ops portal (authentication, SvelteKit CSRF, HMAC signing, schemas, and quirks).
- **[REFLECTION.md](file:///c:/Users/hp/OneDrive/Desktop/Flock%20energy%20assignment/REFLECTION.md)**: Thoughtful responses to the evaluation questions (difficulties, mistakes made, and self-critique).
- **[openapi.json](file:///c:/Users/hp/OneDrive/Desktop/Flock%20energy%20assignment/openapi.json)**: Complete OpenAPI 3.1 specification for programmatic consumption.

---

## Automated Test Suite

A comprehensive test suite verifies models, geospatial math, hierarchy healing, and API endpoints:

```bash
# Run pytest
python -m pytest -v
```

**Results**:
```
tests/test_service.py::test_haversine_distance PASSED
tests/test_service.py::test_parse_urja_timestamp PASSED
tests/test_service.py::test_hierarchy_structure_and_healing PASSED
tests/test_service.py::test_meter_detail_model PASSED
tests/test_api.py::test_health_endpoint PASSED
tests/test_api.py::test_list_meters PASSED
tests/test_api.py::test_list_meters_filtering PASSED
tests/test_api.py::test_get_meter_by_id PASSED
tests/test_api.py::test_get_meter_not_found PASSED
tests/test_api.py::test_get_meter_geo PASSED
tests/test_api.py::test_get_meter_energy PASSED
tests/test_api.py::test_meters_nearby PASSED
tests/test_api.py::test_hierarchy_tree PASSED
tests/test_api.py::test_hierarchy_transformers PASSED
tests/test_api.py::test_transformer_detail PASSED
tests/test_api.py::test_analytics_stats PASSED
tests/test_api.py::test_dashboard_html PASSED

======================== 17 passed in 2.22s ========================
```
