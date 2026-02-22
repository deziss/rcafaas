import os
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("evidence-api")

app = FastAPI(title="RCAFaaS Evidence API", description="Retrieve stored Root Cause Analysis reports")

db_host = os.getenv("DB_HOST", "postgres")
db_name = os.getenv("DB_NAME", "rcafaas")
db_user = os.getenv("DB_USER", "postgres")
db_pass = os.getenv("DB_PASS", "postgres")

def get_db_connection():
    try:
        conn = psycopg2.connect(
            host=db_host,
            database=db_name,
            user=db_user,
            password=db_pass,
            connect_timeout=3
        )
        return conn
    except Exception as e:
        logger.error(f"Could not connect to database: {e}")
        return None

@app.get("/reports")
def get_reports(service: str = None, limit: int = 10):
    """
    Fetch RCA reports, optionally filtering by service name.
    """
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=503, detail="Database currently unavailable")
        
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        if service:
            cur.execute("""
                SELECT * FROM rca_reports 
                WHERE service_name = %s 
                ORDER BY incident_time DESC 
                LIMIT %s
            """, (service, limit))
        else:
            cur.execute("""
                SELECT * FROM rca_reports 
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
            conn.close()

@app.get("/health")
def health():
    conn = get_db_connection()
    if conn:
        conn.close()
        return {"status": "ok", "database": "connected"}
    return {"status": "degraded", "database": "disconnected"}
