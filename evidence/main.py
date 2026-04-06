import os
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool
from fastapi import FastAPI, HTTPException, Query, Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("evidence-api")

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="RCAFaaS Evidence API", description="Retrieve stored Root Cause Analysis reports")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

db_host = os.getenv("DB_HOST", "postgres")
db_name = os.getenv("DB_NAME", "rcafaas")
db_user = os.getenv("DB_USER", "postgres")
db_pass = os.getenv("DB_PASS", "postgres")

db_pool = None

def get_db_pool():
    global db_pool
    if db_pool is None:
        try:
            db_pool = pool.ThreadedConnectionPool(
                minconn=2,
                maxconn=10,
                host=db_host,
                database=db_name,
                user=db_user,
                password=db_pass,
                connect_timeout=3
            )
            logger.info("Database connection pool created.")
        except Exception as e:
            logger.error(f"Could not create connection pool: {e}")
    return db_pool

def get_db_connection():
    p = get_db_pool()
    if p:
        try:
            return p.getconn()
        except Exception as e:
            logger.error(f"Could not get connection from pool: {e}")
    return None

def return_db_connection(conn):
    p = get_db_pool()
    if p and conn:
        p.putconn(conn)

@app.get("/reports")
@limiter.limit("60/minute")
def get_reports(request: Request, service: str = None, limit: int = Query(default=10, ge=1, le=100)):
    """
    Fetch RCA reports, optionally filtering by service name.
    """
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=503, detail="Database currently unavailable")
        
    try:
        REPORT_COLUMNS = """
            id, service_name, incident_time, exit_code, root_cause,
            confidence_score, cpu_usage, memory_usage, disk_io,
            network_drops, evidence_logs, analyzed_at
        """
        cur = conn.cursor(cursor_factory=RealDictCursor)
        if service:
            cur.execute(f"""
                SELECT {REPORT_COLUMNS} FROM rca_reports
                WHERE service_name = %s
                ORDER BY incident_time DESC
                LIMIT %s
            """, (service, limit))
        else:
            cur.execute(f"""
                SELECT {REPORT_COLUMNS} FROM rca_reports
                ORDER BY incident_time DESC
                LIMIT %s
            """, (limit,))
            
        reports = cur.fetchall()
        cur.close()
        
        # Convert datetime objects to strings
        for r in reports:
            for k, v in r.items():
                if hasattr(v, "isoformat"):
                    r[k] = v.isoformat()
                    
        return {"reports": reports}
    except psycopg2.Error as e:
        logger.error(f"Database query error: {e}")
        raise HTTPException(status_code=500, detail="Database query failed")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        if conn:
            return_db_connection(conn)

@app.get("/health")
def health():
    conn = get_db_connection()
    if conn:
        return_db_connection(conn)
        return {"status": "ok", "database": "connected"}
    return {"status": "degraded", "database": "disconnected"}
