# Operations Guide

## Deployment

### Prerequisites

- Docker Engine 20.10+ with Compose v2
- 4 CPU cores and 2GB RAM minimum (see resource budgets in [Architecture](ARCHITECTURE.md))

### Start All Services

```bash
docker compose up --build -d
```

### Verify Deployment

```bash
# All containers should show "healthy"
docker compose ps

# Check individual health endpoints
curl -s http://localhost:8000/health | jq .
curl -s http://localhost:8001/health | jq .

# Verify worker is running
docker logs rca-inference-worker --tail 5
```

### Stop Services

```bash
docker compose down          # stop, preserve volumes
docker compose down -v       # stop and delete volumes (data loss)
```

---

## Environment Variables

### Ingestion API (`rca-api`)

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_HOST` | `localhost` | Redis hostname |
| `REDIS_PORT` | `6379` | Redis port |
| `RCA_QUEUE_NAME` | `rca_events` | Redis list name for event queue |

### Inference Worker (`rca-inference-worker`)

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_HOST` | `localhost` | Redis hostname |
| `REDIS_PORT` | `6379` | Redis port |
| `RCA_QUEUE_NAME` | `rca_events` | Redis list name for event queue |
| `DB_HOST` | `postgres` | PostgreSQL hostname |
| `DB_NAME` | `rcafaas` | Database name |
| `DB_USER` | `postgres` | Database user |
| `DB_PASS` | `postgres` | Database password |
| `PROMETHEUS_URL` | `""` (disabled) | Prometheus base URL (e.g., `http://prometheus:9090`) |
| `OPENOBSERVE_URL` | `""` (disabled) | OpenObserve base URL (e.g., `http://openobserve:5080`) |

### Evidence API (`rca-evidence-api`)

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_HOST` | `postgres` | PostgreSQL hostname |
| `DB_NAME` | `rcafaas` | Database name |
| `DB_USER` | `postgres` | Database user |
| `DB_PASS` | `postgres` | Database password |

### PostgreSQL (`rca-postgres`)

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_DB` | `rcafaas` | Database to create on first start |
| `POSTGRES_USER` | `postgres` | Superuser name |
| `POSTGRES_PASSWORD` | `postgres` | Superuser password |

> **Security:** Default credentials are for development only. In production, use Docker secrets or an external secret manager.

---

## Health Checks

| Service | Mechanism | Interval | Timeout | Failure Threshold |
|---------|-----------|----------|---------|-------------------|
| Ingestion API | `GET /health` (checks Redis) | 10s | 5s | 3 |
| Inference Worker | File `/tmp/worker_health` heartbeat < 30s | 15s | 5s | 3 |
| Evidence API | `GET /health` (checks PostgreSQL) | 10s | 5s | 3 |
| PostgreSQL | `pg_isready -U postgres` | 5s | 5s | 5 |
| Redis | `redis-cli ping` | 5s | 3s | 3 |

### Worker Health File Format

The inference worker writes `/tmp/worker_health` on each event loop iteration:

```json
{
  "status": "ok",
  "last_heartbeat": 1712400000.123,
  "events_processed": 42,
  "timestamp": "2026-04-06T12:00:00.123456"
}
```

---

## Monitoring

### Key Metrics to Watch

| Metric | Source | Warning Threshold | Critical Threshold |
|--------|--------|-------------------|-------------------|
| Queue depth | `redis-cli LLEN rca_events` | > 50 | > 200 |
| Processing queue | `redis-cli LLEN rca_events:processing` | > 5 | > 20 (stuck events) |
| Report count | `SELECT count(*) FROM rca_reports` | -- | -- |
| Worker heartbeat age | `/tmp/worker_health` | > 15s | > 30s (triggers restart) |
| API response time | Access logs | p95 > 100ms | p95 > 500ms |
| DB connection pool | Application logs | exhausted warnings | 503 errors |

### Manual Queue Inspection

```bash
# Check queue depth
docker exec rca-redis redis-cli LLEN rca_events

# Check in-flight events
docker exec rca-redis redis-cli LLEN rca_events:processing

# Peek at next event without consuming
docker exec rca-redis redis-cli LINDEX rca_events -1

# Check DB report count
docker exec rca-postgres psql -U postgres -d rcafaas \
  -c "SELECT count(*), max(analyzed_at) FROM rca_reports;"
```

---

## Troubleshooting

### Worker not processing events

**Symptoms:** Queue depth growing, no new reports.

1. Check worker logs: `docker logs rca-inference-worker --tail 50`
2. Check if worker is healthy: `docker inspect rca-inference-worker --format='{{.State.Health.Status}}'`
3. Check Redis connectivity: `docker exec rca-redis redis-cli ping`
4. Check for orphaned events: `docker exec rca-redis redis-cli LLEN rca_events:processing`
   - If > 0 and worker is running, events may be stuck. Restart the worker to trigger orphan recovery.

### Ingestion API returning 503

**Symptoms:** `POST /report` returns `"RCA queue is temporarily unavailable"`.

1. Check Redis: `docker exec rca-redis redis-cli ping`
2. Check Redis container: `docker ps | grep redis`
3. If Redis is restarting, check memory limits: `docker stats rca-redis`

### Evidence API returning 503

**Symptoms:** `GET /reports` returns `"Database currently unavailable"`.

1. Check PostgreSQL: `docker exec rca-postgres pg_isready -U postgres`
2. Check connection pool logs: `docker logs rca-evidence-api --tail 20`
3. If pool exhausted, increase `maxconn` in `evidence/main.py` or check for connection leaks.

### Duplicate reports

Reports are deduplicated by idempotency key (`SHA-256(service:timestamp:exit_code)`). If duplicates appear, the events have different timestamps (each API call generates a unique timestamp).

### High memory usage on worker

The inference worker loads metric data into pandas DataFrames and runs PyRCA. If processing many events in rapid succession:

1. Check container stats: `docker stats rca-inference-worker`
2. Memory limit is 512MB. PyRCA with large DataFrames can be memory-intensive.
3. If OOM-killed, increase memory limit in `docker-compose.yml`.

---

## Database Maintenance

### Manual Schema Migration

The `db/init.py` script runs automatically on first PostgreSQL start. For manual execution:

```bash
docker exec rca-postgres psql -U postgres -d rcafaas -c "\dt+"
```

### Backup

```bash
# Full database dump
docker exec rca-postgres pg_dump -U postgres rcafaas > backup_$(date +%Y%m%d).sql

# Restore
cat backup_20260406.sql | docker exec -i rca-postgres psql -U postgres -d rcafaas
```

### Data Retention

No automatic retention is configured. To clean old reports:

```sql
DELETE FROM rca_reports WHERE analyzed_at < NOW() - INTERVAL '90 days';
VACUUM ANALYZE rca_reports;
```

---

## Scaling Considerations

| Scenario | Action |
|----------|--------|
| Queue depth consistently > 100 | Add more worker replicas (safe with RPOPLPUSH) |
| Evidence API latency > 200ms | Add read replica; point Evidence API to replica |
| > 1M reports in database | Add `analyzed_at` partitioning; consider archival |
| Multiple environments | Use `RCA_QUEUE_NAME` env var to namespace queues |

### Running Multiple Workers

The reliable queue pattern (RPOPLPUSH) is safe for multiple consumers. To scale:

```yaml
# In docker-compose.yml, add:
inference-worker:
  deploy:
    replicas: 3
```

Remove the `container_name` field (conflicts with replicas).
