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
                service_name VARCHAR(100) NOT NULL,
                incident_time TIMESTAMP NOT NULL,
                exit_code VARCHAR(10) NOT NULL,
                root_cause VARCHAR(150) NOT NULL,
                confidence_score FLOAT NOT NULL,
                cpu_usage FLOAT,
                memory_usage FLOAT,
                disk_io FLOAT,
                network_drops INT,
                analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
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
