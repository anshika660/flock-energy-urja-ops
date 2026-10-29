# REFLECTION.md — Engineering Reflections & Review

## 1. What assumptions did you make?
- **Read-Only Upstream Nature**: Per the problem specification and operational reality of utility metering, we assumed the Urja portal is an operational read-only source of truth. The gateway does not attempt mutation of meter states.
- **Dataset Size & Volatility**: With 403 meters across 40 Distribution Transformers in Jaipur Discom, the entire fleet fits comfortably in memory (< 5 MB RAM). We assumed meter nameplates and hierarchy topologies change infrequently (hours/days), while energy interval readings represent historic window data. An in-memory cache with a 300-second TTL backed by background pre-warming provides optimal balance between upstream protection and sub-millisecond API response latency.
- **Canonical 1-to-1 Hierarchy Mapping**: We assumed each named electrical entity (e.g. "Circle 6", "Feeder 5") has a unique canonical code (e.g. "C-06", "F-005"). Cross-validation across all 403 meter records proved this assumption holds 100% true, allowing deterministic self-healing of the 22 corrupted records.
- **Client Session Longevity**: The portal issues cookies with a 1-hour (`Max-Age=3600`) lifetime. We assumed sessions could expire or be terminated upstream at any moment, necessitating transparent re-authentication without dropping client requests.

---

## 2. Which part was the most difficult, and how did you get unstuck?
The most challenging part was reverse-engineering the authentication and bulk export mechanisms:
1. **Initial 403 Forbidden on Login**: Sending a standard `POST /login` with valid credentials was rejected with `403 Forbidden: Cross-site POST form submissions are forbidden`. Because SvelteKit enforces CSRF origin validation on server form actions, inspecting the network requests revealed that SvelteKit requires both `Origin: https://urja-ops.flockenergy.tech` and `Referer: https://urja-ops.flockenergy.tech/login`. Replicating these headers resolved authentication immediately.
2. **Reverse-Engineering the HMAC-SHA256 Signing for Bulk Export**: The portal's UI had an "Export all meters" button. By downloading and beautifying the client bundle (`4.2Bgc2kUI.js`), we discovered that the client called `/portal/keys` to retrieve a dynamic `signingSecret`, constructed a canonical signature payload `f"{method}\n{path}\n{query}\n{timestamp}"`, and signed it using WebCrypto HMAC-SHA256. Re-implementing this signing protocol in Python unlocked the bulk export endpoint, allowing us to fetch the full 403-meter dataset with complete hierarchy and coordinates in a single call instead of paginating 20 items at a time.

---

## 3. If you had another day, what would you improve?
1. **Persistent Backing Store (PostgreSQL / PostGIS & Redis)**: While in-memory caching is ideal for 403 meters, scaling to millions of meters across state-wide DISCOMs requires a persistent relational store with PostGIS geospatial indexes (R-Tree / GiST) and Redis for distributed caching.
2. **Time-Series Database for Energy Readings**: Smart meter telemetry (15-min or 30-min interval readings) scales to hundreds of millions of rows. Storing consumption data in TimescaleDB or ClickHouse would enable rolling aggregations, continuous aggregate views, and instant long-term anomaly detection.
3. **Webhook & Event-Driven Change Capture**: If the utility portal supported event streaming (or if we set up a background polling change-detector), we could emit webhooks whenever a meter transitions from `Installed` to `Faulty` or `Decommissioned`.
4. **OAuth2 / API Key Rate Limiting**: Introduce client authentication (JWT tokens or API keys) and token-bucket rate limiting to secure the gateway itself.

---

## 4. What mistake did you make while solving this (there's always one)?
During our initial test suite run, we encountered `RuntimeError: Event loop is closed` when executing asynchronous calls with Starlette's synchronous `TestClient`. We had initialized `httpx.AsyncClient` inside `UrjaPortalClient.__init__`, binding the HTTP transport to the event loop created during module import. When Starlette's `TestClient` spawned independent event loops for subsequent test cases, `httpx` attempted to execute connections against the dead loop.

We diagnosed this by inspecting the call stack down into `httpcore._backends.anyio` and corrected it by making client acquisition dynamic: `_get_client()` now checks `asyncio.get_running_loop()`, tearing down and recreating the underlying transport whenever the active event loop changes while preserving the session cookie. This made the client test suite 100% resilient across multi-threaded and multi-loop test runners.

---

## 5. If you were reviewing your own submission, what would you criticise?
1. **Coupling to Portal's Internal Export Endpoint**: Our service heavily leverages `/portal/export` for fast synchronization. While this provides extraordinary performance benefits and complete data fidelity, it relies on an undocumented endpoint and dynamic HMAC signing secret that the utility could change in a future portal deployment. A truly defensive architecture would include a fallback scraping pipeline that crawls `/meters` and `/portal/meters/search` if `/portal/export` ever fails.
2. **Energy Ingestion On-Demand**: While meter metadata is synchronized in bulk, interval energy readings are fetched on-demand per meter. For 403 meters this is fast, but if a client requested batch energy profiles for hundreds of meters simultaneously, the upstream portal would receive hundreds of sequential requests. Implementing a batch pre-fetcher or async queue with concurrency throttling (e.g. `asyncio.Semaphore(10)`) would better protect the upstream server from being overwhelmed.
3. **Single In-Memory Process**: Because the local query index lives in Python process memory, running multiple Uvicorn worker processes (`--workers 4`) would cause each worker to maintain its own independent cache copy and sync cycle. Moving the index to Redis or SQLite would ensure cross-worker consistency.

