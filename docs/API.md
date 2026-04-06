# API Reference

RCAFaaS exposes two HTTP services: the **Ingestion API** (port 8000) for submitting crash reports, and the **Evidence API** (port 8001) for retrieving RCA results.

Both APIs use JSON request/response bodies and return standard HTTP status codes.

---

## Ingestion API (port 8000)

### POST /report

Submit a service crash incident for root cause analysis.

**Rate limit:** 30 requests/minute per IP.

#### Request

```http
POST /report HTTP/1.1
Content-Type: application/json

{
  "service": "auth-service",
  "exit_code": "137"
}
```

| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| `service` | string | yes | `min_length=1` | Name of the crashed service |
| `exit_code` | string | yes | -- | Process exit code (e.g., `"137"` for OOM kill, `"1"` for generic error) |

#### Response -- 200 OK

```json
{
  "status": "processing",
  "message": "Incident report received for service 'auth-service'",
  "timestamp": "2026-04-06T12:00:00.000000+00:00",
  "exit_code": "137"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Always `"processing"` on success |
| `message` | string | Human-readable confirmation |
| `timestamp` | string (ISO 8601) | UTC timestamp when the report was received |
| `exit_code` | string | Echo of submitted exit code |

#### Error Responses

| Status | Condition | Body |
|--------|-----------|------|
| `422` | Missing or invalid fields (empty `service`, missing `exit_code`) | Pydantic validation error detail |
| `429` | Rate limit exceeded (>30 req/min from same IP) | `Retry-After` header included |
| `503` | Redis queue unavailable | `{"detail": "RCA queue is temporarily unavailable"}` |

#### Behavior

1. Validates the request body via Pydantic.
2. Pings Redis to verify queue availability. Returns 503 if unreachable.
3. Returns 200 immediately and enqueues the event asynchronously via FastAPI `BackgroundTasks`.
4. The background task retries Redis publish up to 3 times with 1-second delays. If all retries fail, the event is logged as lost.

> **Note:** A 200 response confirms the report was *accepted*, not that analysis is complete. The RCA result becomes available via the Evidence API after the inference worker processes it.

---

### GET /health

Health check for the Ingestion API.

#### Response -- 200 OK

```json
{"status": "ok", "redis": "connected"}
```

Or, when Redis is unreachable:

```json
{"status": "degraded", "redis": "disconnected"}
```

> Always returns 200. Use the `status` field to determine service health. The health check actively pings Redis.

---

## Evidence API (port 8001)

### GET /reports

Retrieve stored RCA reports, optionally filtered by service name.

**Rate limit:** 60 requests/minute per IP.

#### Query Parameters

| Parameter | Type | Default | Constraints | Description |
|-----------|------|---------|-------------|-------------|
| `service` | string | `null` | -- | Filter by service name (exact match) |
| `limit` | integer | `10` | `1 <= limit <= 100` | Maximum number of reports to return |

#### Request Examples

```bash
# Get latest 10 reports across all services
curl http://localhost:8001/reports

# Filter by service, limit to 5
curl "http://localhost:8001/reports?service=auth-service&limit=5"
```

#### Response -- 200 OK

```json
{
  "reports": [
    {
      "id": 1,
      "service_name": "auth-service",
      "incident_time": "2026-04-06T12:00:00",
      "exit_code": "137",
      "root_cause": "cpu_usage",
      "confidence_score": 0.85,
      "cpu_usage": 92.5,
      "memory_usage": 45.2,
      "disk_io": 12.3,
      "network_drops": 3,
      "evidence_logs": "ERROR [2026-04-06T12:00:00] auth-service: unhandled exception...",
      "analyzed_at": "2026-04-06T12:00:05"
    }
  ]
}
```

#### Report Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | integer | Auto-incrementing report ID |
| `service_name` | string | Name of the crashed service |
| `incident_time` | string (ISO 8601) | When the crash was reported |
| `exit_code` | string | Process exit code |
| `root_cause` | string | Identified root cause (metric name or descriptive label) |
| `confidence_score` | float (0.0-0.99) | Confidence in the root cause identification |
| `cpu_usage` | float or null | CPU usage at crash time (percentage) |
| `memory_usage` | float or null | Memory usage at crash time (percentage) |
| `disk_io` | float or null | Disk I/O metric at crash time |
| `network_drops` | integer or null | Dropped network packets count |
| `evidence_logs` | string or null | Log lines collected around the crash |
| `analyzed_at` | string (ISO 8601) | When the analysis was completed |

#### Error Responses

| Status | Condition | Body |
|--------|-----------|------|
| `422` | Invalid `limit` (< 1, > 100, or non-integer) | Pydantic validation error detail |
| `429` | Rate limit exceeded | `Retry-After` header included |
| `500` | Database query error | `{"detail": "Database query failed"}` |
| `503` | Database unavailable | `{"detail": "Database currently unavailable"}` |

#### Behavior

Reports are returned in reverse chronological order (`incident_time DESC`). The query uses a composite index on `(service_name, incident_time DESC)` for efficient filtering.

---

### GET /health

Health check for the Evidence API.

#### Response -- 200 OK

```json
{"status": "ok", "database": "connected"}
```

Or, when the database is unreachable:

```json
{"status": "degraded", "database": "disconnected"}
```

---

## Internal Event Schema

Events on the Redis queue (`rca_events`) use the following JSON structure. This is an internal contract between the Ingestion API and Inference Worker.

```json
{
  "service": "auth-service",
  "exit_code": "137",
  "timestamp": "2026-04-06T12:00:00.000000+00:00",
  "status": "pending_analysis"
}
```

---

## Idempotency

Duplicate crash events (same `service` + `timestamp` + `exit_code`) are deduplicated at the database level using a SHA-256 idempotency key. The second insertion is silently ignored (`ON CONFLICT DO NOTHING`).

---

## Common Exit Codes

| Code | Signal | Typical Cause |
|------|--------|---------------|
| `1` | -- | Generic application error |
| `137` | SIGKILL | OOM killer or forced termination |
| `139` | SIGSEGV | Segmentation fault |
| `143` | SIGTERM | Graceful shutdown requested |
