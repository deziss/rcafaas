# RCAFaaS

**Root Cause Analysis as a Service** -- automated incident root cause discovery using causal inference.

RCAFaaS receives service crash reports, correlates metrics and logs from observability backends, applies the PC causal discovery algorithm ([PyRCA](https://github.com/salesforce/PyRCA)), and produces an RCA report with a confidence score. It is designed to accelerate post-mortem analysis and reduce mean-time-to-resolution (MTTR) for distributed systems.

## Architecture

```
                        +-----------------+
  POST /report  ------> | Ingestion API   |----> Redis Queue (rca_events)
  (port 8000)           +-----------------+            |
                                                       v
                                               +------------------+
                                               | Inference Worker |
                                               | (PyRCA / PC alg) |
                                               +------------------+
                                                       |
                                                       v
  GET /reports  ------> +-----------------+     +------------+
  (port 8001)           | Evidence API    |<----| PostgreSQL |
                        +-----------------+     +------------+
```

## Quickstart

```bash
# Clone and start all services
docker compose up --build -d

# Submit a crash report
curl -X POST http://localhost:8000/report \
  -H "Content-Type: application/json" \
  -d '{"service": "auth-service", "exit_code": "137"}'

# Retrieve RCA reports (wait a few seconds for processing)
curl http://localhost:8001/reports?service=auth-service

# Check service health
curl http://localhost:8000/health
curl http://localhost:8001/health
```

## Services

| Service | Port | Description |
|---------|------|-------------|
| **Ingestion API** | 8000 | Accepts crash reports, enqueues for analysis |
| **Inference Worker** | -- | Consumes events, runs causal inference, writes results |
| **Evidence API** | 8001 | Read-only API to retrieve RCA reports |
| **PostgreSQL** | internal | Stores RCA reports |
| **Redis** | internal | Event queue with AOF persistence |

## Documentation

- [API Reference](docs/API.md) -- endpoint schemas, error codes, rate limits
- [Architecture](docs/ARCHITECTURE.md) -- system design, data flow, failure modes
- [Operations](docs/OPERATIONS.md) -- configuration, deployment, monitoring, troubleshooting
- [Development](docs/DEVELOPMENT.md) -- local setup, testing, contributing

## Tech Stack

- **Python 3.11** / FastAPI / Uvicorn
- **PostgreSQL 15** -- report storage with idempotency deduplication
- **Redis 7** -- event queue with AOF persistence and reliable queue pattern
- **PyRCA** (Salesforce) -- PC algorithm for causal graph discovery
- **Docker Compose** -- orchestration with health checks and resource limits

## Testing

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r tests/requirements.txt -r ingestion/requirements.txt -r evidence/requirements.txt -r inference/requirements.txt
pytest tests/ -v
```

34 tests covering ingestion API, evidence API, and worker unit logic.

## License

Private -- internal use only.
