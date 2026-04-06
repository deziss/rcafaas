import os
import json
import logging
import time
from datetime import datetime, timezone
from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import redis

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="RCAFaaS Ingestion Service", description="Receives crash events and triggers RCA logic")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

redis_host = os.getenv("REDIS_HOST", "localhost")
redis_port = int(os.getenv("REDIS_PORT", 6379))
QUEUE_NAME = os.getenv("RCA_QUEUE_NAME", "rca_events")

# Create Redis connection pool
redis_pool = redis.ConnectionPool(host=redis_host, port=redis_port, decode_responses=True)

class IncidentReport(BaseModel):
    service: str = Field(..., description="Name of the service that crashed", min_length=1)
    exit_code: str = Field(..., description="Exit code of the service")

def get_redis_client():
    return redis.Redis(connection_pool=redis_pool)

def trigger_rca_pipeline(service_name: str, exit_code: str, timestamp: str):
    """
    Publish an event to Redis so the Inference worker can process the RCA.
    """
    max_retries = 3
    for attempt in range(max_retries):
        try:
            r = get_redis_client()
            payload = {
                "service": service_name,
                "exit_code": exit_code,
                "timestamp": timestamp,
                "status": "pending_analysis"
            }
            # Push the incident to a queue for the inferencer
            r.lpush(QUEUE_NAME, json.dumps(payload))
            logger.info(f"Triggered RCA for {service_name} at {timestamp}. exit_code={exit_code}")
            return
        except redis.ConnectionError as e:
            logger.warning(f"Failed to connect to Redis (attempt {attempt+1}/{max_retries}): {e}")
            time.sleep(1)
        except Exception as e:
            logger.error(f"Unexpected error publishing to Redis: {e}")
            break
            
    logger.error(f"Could not publish RCA trigger for {service_name} after {max_retries} attempts. Event lost.")

@app.post("/report")
@limiter.limit("30/minute")
async def report_incident(
    request: Request,
    report: IncidentReport,
    background_tasks: BackgroundTasks
):
    # Verify Redis is healthy before accepting the report
    try:
        r = get_redis_client()
        r.ping()
    except redis.ConnectionError:
        logger.error("Rejecting report: Redis queue is currently unavailable")
        raise HTTPException(status_code=503, detail="RCA queue is temporarily unavailable")

    timestamp = datetime.now(timezone.utc).isoformat()
    # Trigger the RCA inference worker asynchronously
    background_tasks.add_task(trigger_rca_pipeline, report.service, report.exit_code, timestamp)
    
    return {
        "status": "processing",
        "message": f"Incident report received for service '{report.service}'",
        "timestamp": timestamp,
        "exit_code": report.exit_code
    }

@app.get("/health")
def health():
    try:
        r = get_redis_client()
        r.ping()
        return {"status": "ok", "redis": "connected"}
    except Exception:
        return {"status": "degraded", "redis": "disconnected"}
