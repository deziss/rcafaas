# Development Guide

## Project Structure

```
rcafaas/
  ingestion/              # Ingestion API (FastAPI, port 8000)
    main.py               # POST /report, GET /health
    Dockerfile
    requirements.txt
  inference/              # Inference Worker (long-running process)
    worker.py             # Event loop, PyRCA causal inference, circuit breakers
    Dockerfile
    requirements.txt
  evidence/               # Evidence API (FastAPI, port 8001)
    main.py               # GET /reports, GET /health
    Dockerfile
    requirements.txt
  db/
    init.py               # Schema creation and migrations
    requirements.txt
  tests/
    test_ingestion_api.py # 10 tests: validation, health, rate limiting
    test_evidence_api.py  # 10 tests: queries, limits, health, errors
    test_worker_unit.py   # 14 tests: idempotency, circuit breaker, causal scoring
    requirements.txt
  docs/
    API.md                # Endpoint reference
    ARCHITECTURE.md       # System design
    OPERATIONS.md         # Deployment and runbook
    DEVELOPMENT.md        # This file
  docker-compose.yml
  pytest.ini
  .dockerignore
  README.md
```

## Local Setup

### Option 1: Full Stack with Docker (recommended)

```bash
docker compose up --build -d
```

This starts all 5 services. Use `docker compose logs -f` to tail logs.

### Option 2: Run Services Individually

For faster iteration on a single service:

```bash
# Start dependencies
docker compose up -d postgres redis

# Run a specific service locally
cd ingestion
pip install -r requirements.txt
REDIS_HOST=localhost uvicorn main:app --reload --port 8000
```

### Debug Ports

To access PostgreSQL or Redis directly for debugging, uncomment the port mappings in `docker-compose.yml`:

```yaml
postgres:
  ports:
    - "5432:5432"   # uncomment this line

redis:
  ports:
    - "6379:6379"   # uncomment this line
```

Then connect with:

```bash
psql -h localhost -U postgres -d rcafaas
redis-cli -h localhost
```

## Testing

### Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r tests/requirements.txt \
            -r ingestion/requirements.txt \
            -r evidence/requirements.txt \
            -r inference/requirements.txt
```

### Run Tests

```bash
# All tests
pytest tests/ -v

# Specific test file
pytest tests/test_worker_unit.py -v

# Specific test class
pytest tests/test_evidence_api.py::TestReportsEndpoint -v

# With coverage (requires pytest-cov)
pytest tests/ --cov=ingestion --cov=evidence --cov=inference
```

### Test Architecture

Tests use **FastAPI TestClient** and **unittest.mock** -- no running services required.

| File | Type | What It Tests |
|------|------|---------------|
| `test_ingestion_api.py` | Integration | POST /report validation (200, 422, 503), GET /health, rate limiting |
| `test_evidence_api.py` | Integration | GET /reports with filters and limits, error responses, GET /health |
| `test_worker_unit.py` | Unit | `make_idempotency_key()` determinism, `CircuitBreaker` state transitions, `calculate_causal_score()` with various metric profiles |

### Module Import Note

Since both APIs have `main.py` files, the tests use `importlib.util.spec_from_file_location()` to load each module with a unique name, avoiding Python's module cache collision.

## Adding a New Endpoint

1. Add the route in the relevant `main.py` file
2. Add Pydantic model for request validation
3. Add `@limiter.limit()` decorator with appropriate rate
4. Add at least these test cases:
   - Valid request (200)
   - Missing/invalid input (422)
   - Dependency unavailable (503)
5. Update `docs/API.md` with request/response schema

## Adding a New Metric to the Worker

1. Add the metric name to the `metrics` list in `discover_metrics()` ([worker.py:142](../inference/worker.py))
2. Add corresponding mock data generation below the Prometheus query block
3. Add a column to the `rca_reports` table in `db/init.py` (with `ADD COLUMN IF NOT EXISTS` migration)
4. Update the `INSERT INTO` statement in `save_to_db()`
5. Update the `REPORT_COLUMNS` constant in `evidence/main.py`
6. Update `docs/API.md` report field table

## Code Conventions

- **Logging:** Use module-level `logger` instance, never `print()`. Structured format: `%(asctime)s - %(name)s - %(levelname)s - %(message)s`
- **Database queries:** Always use parameterized queries (`%s` placeholders). Never string-interpolate user input.
- **Environment config:** All external URLs, credentials, and tuning knobs via environment variables with sensible defaults.
- **Error handling:** Catch specific exceptions. Log with context. Re-raise or return appropriate HTTP status. Never swallow silently.
- **Queue names:** Use `RCA_QUEUE_NAME` env var. Processing queue is always `{QUEUE_NAME}:processing`.

## Dependencies

### Ingestion API

| Package | Version | Purpose |
|---------|---------|---------|
| fastapi | 0.104.1 | HTTP framework |
| uvicorn | 0.24.0 | ASGI server |
| redis | 5.0.1 | Queue client |
| pydantic | 2.4.2 | Request validation |
| slowapi | 0.1.9 | Rate limiting |

### Inference Worker

| Package | Version | Purpose |
|---------|---------|---------|
| redis | 5.0.1 | Queue consumer |
| psycopg2-binary | 2.9.9 | PostgreSQL client |
| requests | 2.31.0 | HTTP client for Prometheus/OpenObserve |
| pandas | 2.1.2 | Time-series data manipulation |
| sfr-pyrca | 1.0.1 | Causal discovery (PC algorithm) |
| networkx | >= 2.6 | Graph algorithms |
| scikit-learn | >= 0.24 | ML utilities |
| scipy | >= 1.4.1 | Statistical computing |

### Evidence API

| Package | Version | Purpose |
|---------|---------|---------|
| fastapi | 0.104.1 | HTTP framework |
| uvicorn | 0.24.0 | ASGI server |
| psycopg2-binary | 2.9.9 | PostgreSQL client (with connection pooling) |
| pydantic | 2.4.2 | Query validation |
| slowapi | 0.1.9 | Rate limiting |

## Docker

All services run as non-root user `appuser`. Images use `python:3.11-slim` as base. The `.dockerignore` excludes tests, `__pycache__`, `.git`, and markdown files from the build context.

### Rebuilding a Single Service

```bash
docker compose build inference-worker
docker compose up -d inference-worker
```
