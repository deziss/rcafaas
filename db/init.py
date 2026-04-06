import os
import time
import logging
import psycopg2

logger = logging.getLogger("rca-db-init")
logging.basicConfig(level=logging.INFO)

db_host = os.getenv("DB_HOST", "postgres")
db_name = os.getenv("DB_NAME", "rcafaas")
db_user = os.getenv("DB_USER", "postgres")
db_pass = os.getenv("DB_PASS", "postgres")

def init_db():
    conn = None
    while True:
        try:
            conn = psycopg2.connect(
                host=db_host,
                database=db_name,
                user=db_user,
                password=db_pass
            )
            break
        except Exception as e:
            logger.warning(f"Waiting for Postgres... {e}")
            time.sleep(2)
            
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS rca_reports (
                id SERIAL PRIMARY KEY,
                idempotency_key VARCHAR(32) UNIQUE,
                service_name VARCHAR(100) NOT NULL,
                incident_time TIMESTAMP NOT NULL,
                exit_code VARCHAR(10) NOT NULL,
                root_cause TEXT NOT NULL,
                confidence_score FLOAT NOT NULL,
                cpu_usage FLOAT,
                memory_usage FLOAT,
                disk_io FLOAT,
                network_drops INT,
                evidence_logs TEXT,
                analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # Incremental schema migrations for existing tables
        cur.execute("""
            ALTER TABLE rca_reports
            ADD COLUMN IF NOT EXISTS evidence_logs TEXT;
        """)
        cur.execute("""
            ALTER TABLE rca_reports
            ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(32);
        """)

        # Unique constraint for deduplication
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_rca_reports_idempotency
            ON rca_reports (idempotency_key)
            WHERE idempotency_key IS NOT NULL;
        """)

        # Index for the primary query pattern: filter by service, order by time
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_rca_reports_service_time
            ON rca_reports (service_name, incident_time DESC);
        """)
        conn.commit()
        cur.close()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Error initializing DB schema: {e}")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    init_db()
