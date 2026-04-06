# Architecture

## System Overview

RCAFaaS is a 3-tier event-driven microservices system for automated root cause analysis of service crashes. It decouples incident reporting from analysis processing via a Redis queue, allowing the compute-intensive causal inference to run asynchronously without blocking API callers.

```
                   +-------------------+
                   |    Clients /      |
                   |    Alertmanager   |
                   +--------+----------+
                            |
                       POST /report
                            |
                            v
                   +-------------------+        +------------------+
                   |  Ingestion API    |------->|     Redis 7      |
                   |  (FastAPI :8000)  |  lpush |  AOF persistence |
                   +-------------------+        +--------+---------+
                                                         |
                                                    rpoplpush
                                                         |
                                                         v
                                                +------------------+
                   +------------------+         | Inference Worker |
                   |   Prometheus     |<--------|  (PyRCA / PC)    |
                   +------------------+         +--------+---------+
                   +------------------+                  |
                   |   OpenObserve    |<---------+       | INSERT
                   +------------------+          |       v
                                                 |  +-----------+
                   +-------------------+         |  | PostgreSQL|
                   |  Evidence API     |-------->|  |    15     |
                   |  (FastAPI :8001)  | SELECT  |  +-----------+
                   +-------------------+         |
                            ^                    |
                       GET /reports              |
                            |                    |
                   +--------+----------+         |
                   |    Clients /      +---------+
                   |    Dashboards     |
                   +-------------------+
```

## Components

### Ingestion API

- **Tech:** FastAPI + Uvicorn, Python 3.11
- **Responsibility:** Accept crash reports, validate input, enqueue for processing
- **Port:** 8000
- **Stateless:** Yes -- uses Redis connection pool, no local state
- **Rate limit:** 30 req/min per IP on POST /report
- **Resource limits:** 0.5 CPU, 256MB RAM

### Inference Worker

- **Tech:** Python 3.11, PyRCA (PC algorithm), pandas
- **Responsibility:** Consume events from Redis, fetch metrics/logs, run causal inference, store results
- **Stateless:** Yes -- all state in Redis and PostgreSQL
- **Queue pattern:** Reliable queue via `RPOPLPUSH` with orphan recovery on startup
- **Circuit breakers:** Prometheus (3 failures / 60s cooldown), OpenObserve (3 failures / 60s cooldown)
- **Health:** File-based heartbeat at `/tmp/worker_health`, checked every 15s by Docker
- **Resource limits:** 1.0 CPU, 512MB RAM
- **Graceful shutdown:** Handles SIGTERM/SIGINT, completes current event before exiting

### Evidence API

- **Tech:** FastAPI + Uvicorn, Python 3.11
- **Responsibility:** Read-only query interface for RCA reports
- **Port:** 8001
- **Connection pooling:** `ThreadedConnectionPool` (min=2, max=10)
- **Rate limit:** 60 req/min per IP on GET /reports
- **Resource limits:** 0.5 CPU, 256MB RAM

### PostgreSQL 15

- **Role:** Primary data store for RCA reports
- **Persistence:** Docker volume `rca_pg_data`
- **Schema:** Single table `rca_reports` with idempotency key and composite index
- **Network:** Internal only (no host port exposed)

### Redis 7

- **Role:** Event queue between ingestion and inference
- **Persistence:** AOF with `appendfsync everysec`
- **Queues:** `rca_events` (main), `rca_events:processing` (in-flight)
- **Network:** Internal only (no host port exposed)

## Data Flow

### Happy Path

```
1. Client sends POST /report with {service, exit_code}
2. Ingestion API validates, pings Redis, returns 200
3. Background task LPUSHes event to rca_events queue
4. Inference Worker RPOPLPUSHes event to rca_events:processing
5. Worker fetches metrics from Prometheus (5-min window before crash)
6. Worker fetches logs from OpenObserve
7. Worker runs PyRCA PC algorithm on metrics DataFrame
8. Worker identifies root cause (highest out-degree anomalous node)
9. Worker INSERTs report with idempotency key (ON CONFLICT DO NOTHING)
10. Worker LREMs event from processing queue
11. Client queries GET /reports to retrieve results
```

### Fallback Path

When Prometheus or OpenObserve are unavailable (or circuit breaker is open):

- **Metrics:** Mock time-series data with synthetic causal relationships and injected anomalies
- **Logs:** Mock log lines simulating crash sequence
- **Inference:** If PyRCA fails, falls back to threshold-based heuristics (memory > 70% = OOM, CPU > 70% = starvation, etc.)

## Database Schema

```sql
CREATE TABLE rca_reports (
    id                SERIAL PRIMARY KEY,
    idempotency_key   VARCHAR(32) UNIQUE,
    service_name      VARCHAR(100) NOT NULL,
    incident_time     TIMESTAMP NOT NULL,
    exit_code         VARCHAR(10) NOT NULL,
    root_cause        TEXT NOT NULL,
    confidence_score  FLOAT NOT NULL,
    cpu_usage         FLOAT,
    memory_usage      FLOAT,
    disk_io           FLOAT,
    network_drops     INT,
    evidence_logs     TEXT,
    analyzed_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Indexes

| Index | Columns | Purpose |
|-------|---------|---------|
| `idx_rca_reports_idempotency` | `idempotency_key` (partial, WHERE NOT NULL) | Deduplication of crash events |
| `idx_rca_reports_service_time` | `service_name, incident_time DESC` | Efficient filtered queries |

## Reliable Queue Pattern

The inference worker uses the [reliable queue pattern](https://redis.io/docs/latest/develop/use/patterns/reliable-queue/) to prevent event loss:

```
rca_events (main queue)       rca_events:processing (in-flight)
+---+---+---+---+             +---+
| D | C | B | A |  --RPOPLPUSH-->  | A |
+---+---+---+---+             +---+

After successful processing:
  LREM rca_events:processing 1 <payload>

On worker restart (orphan recovery):
  RPOPLPUSH rca_events:processing rca_events  (for each orphan)
```

## Failure Mode Analysis

| Component | Failure | Impact | Detection | Auto-Recovery |
|-----------|---------|--------|-----------|---------------|
| Redis down | Ingestion returns 503; worker blocks | No new events accepted | Health check (ping) | Container restart; AOF preserves queue |
| PostgreSQL down | Evidence API returns 503; worker retries 3x | Reports not saved (retried) | Health check (pg_isready) | Container restart; volume preserves data |
| Worker crash | Events accumulate in queue | Processing delayed | Heartbeat file >30s stale | Container restart; orphan recovery |
| Worker stuck | Same as crash from monitoring perspective | Processing halted | Heartbeat check | Docker health check triggers restart |
| Prometheus down | Falls back to mock metrics | Analysis uses synthetic data | Circuit breaker logs | CB half-opens after 60s timeout |
| OpenObserve down | Falls back to mock logs | Evidence logs are synthetic | Circuit breaker logs | CB half-opens after 60s timeout |

## CAP Tradeoff

The system chooses **AP (Availability + Partition tolerance)**:

- Events are accepted before being analyzed (eventual consistency between ingestion and results)
- If Redis is partitioned from PostgreSQL, the worker accumulates events and processes them when connectivity resumes
- Duplicate events are handled at the database level via idempotency keys

This tradeoff is appropriate for a post-mortem analysis system where latency of results (seconds to minutes) is acceptable.

## Security Boundaries

- **External-facing:** Ingestion API (port 8000) and Evidence API (port 8001)
- **Internal-only:** PostgreSQL and Redis (no host ports exposed)
- **Authentication:** None (must be placed behind an API gateway or network boundary)
- **Rate limiting:** slowapi on both APIs (IP-based)
- **Container isolation:** All services run as non-root (`appuser`)
- **Input validation:** Pydantic models on all endpoints; parameterized SQL queries throughout

## Resource Budgets

| Service | CPU | Memory | Restart Policy |
|---------|-----|--------|----------------|
| Ingestion API | 0.5 | 256MB | always |
| Inference Worker | 1.0 | 512MB | always |
| Evidence API | 0.5 | 256MB | always |
| PostgreSQL | 1.0 | 512MB | always |
| Redis | 0.5 | 256MB | always |
| **Total** | **3.5** | **1.75GB** | |
