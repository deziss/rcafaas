import os
import json
import logging
import time
import random
import redis
import psycopg2

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("rca-inference-worker")

redis_host = os.getenv("REDIS_HOST", "localhost")
redis_port = int(os.getenv("REDIS_PORT", 6379))

db_host = os.getenv("DB_HOST", "postgres")
db_name = os.getenv("DB_NAME", "rcafaas")
db_user = os.getenv("DB_USER", "postgres")
db_pass = os.getenv("DB_PASS", "postgres")

def connect_redis():
    while True:
        try:
            r = redis.Redis(host=redis_host, port=redis_port, decode_responses=True)
            r.ping()
            logger.info("Connected to Redis successfully.")
            return r
        except Exception as e:
            logger.warning(f"Failed to connect to Redis. Retrying in 5 seconds... Error: {e}")
            time.sleep(5)

def save_to_db(service: str, timestamp: str, exit_code: str, root_cause: str, confidence: float, metrics: dict):
    max_retries = 3
    for attempt in range(max_retries):
        conn = None
        try:
            conn = psycopg2.connect(host=db_host, database=db_name, user=db_user, password=db_pass, connect_timeout=5)
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO rca_reports 
                (service_name, incident_time, exit_code, root_cause, confidence_score, cpu_usage, memory_usage, disk_io, network_drops)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                service, timestamp, exit_code, root_cause, confidence,
                metrics.get("cpu_usage"), metrics.get("memory_usage"), 
                metrics.get("disk_io"), metrics.get("network_dropped_packets")
            ))
            conn.commit()
            cur.close()
            logger.info(f"Successfully saved RCA Record for {service} to PostgreSQL.")
            return # Exit on success
        except psycopg2.OperationalError as e:
            logger.warning(f"Database connection issue during save (attempt {attempt+1}/{max_retries}): {e}")
            time.sleep(2)
        except Exception as e:
            logger.error(f"Failed to save to database: {e}")
            break # Non-recoverable error
        finally:
            if conn:
                conn.close()
    logger.error(f"Failed to save RCA record for {service} to PostgreSQL after retries.")

def discover_metrics(service_name: str, crash_time: str):
    """
    Mock function simulating a query to Prometheus/Loki 
    for the 5 minutes preceding the crash.
    """
    logger.info(f"[{service_name}] Querying telemetry data 5 minutes prior to {crash_time}...")
    time.sleep(2) # Simulate network delay
    
    # Mock some data variability
    metrics = {
        "cpu_usage": random.uniform(10, 99),
        "memory_usage": random.uniform(20, 99),
        "disk_io": random.uniform(5, 80),
        "network_dropped_packets": random.randint(0, 1000)
    }
    logger.info(f"[{service_name}] Raw Metrics Retrieved: {metrics}")
    return metrics

def calculate_causal_score(metrics: dict):
    """
    Mock PyRCA / DoWhy causality calculation.
    """
    logger.info("Running Root Cause Analysis (PyRCA Causal Discovery)...")
    time.sleep(3) # Simulate heavy computation
    
    # Simple mock logic based on thresholds
    scores = {}
    if metrics["memory_usage"] > 90:
        scores["Memory Leak (OOM)"] = metrics["memory_usage"] / 100.0
    if metrics["cpu_usage"] > 90:
        scores["CPU Starvation"] = metrics["cpu_usage"] / 100.0
    if metrics["network_dropped_packets"] > 500:
        scores["Network Partition / Saturation"] = min(metrics["network_dropped_packets"] / 1000.0, 0.99)
        
    if not scores:
        scores["Unknown / Application Panic"] = 0.5
        
    # Find the top causal score
    root_cause = max(scores, key=scores.get)
    confidence = scores[root_cause]
    
    return root_cause, confidence

def process_event(r: redis.Redis):
    logger.info("Listening for RCA events...")
    while True:
        try:
            # Block until an event is available in the list
            # Returns a tuple: (list_name, item)
            result = r.brpop("rca_events", timeout=5)
            if not result:
                continue
                
            _, payload_str = result
            
            try:
                event = json.loads(payload_str)
            except json.JSONDecodeError as e:
                logger.error(f"Received malformed payload: {payload_str}. Error: {e}")
                continue # Skip bad payload
            
            service = event.get("service", "unknown_service")
            timestamp = event.get("timestamp", "unknown_time")
            exit_code = event.get("exit_code", "unknown")
            
            logger.info(f"--- NEW INCIDENT REPORT RECEIVED ---")
            logger.info(f"Service: {service} | Exit Code: {exit_code} | Time: {timestamp}")
            
            # Step 1: Data Discovery
            metrics = discover_metrics(service, timestamp)
            
            # Step 2: Causal Inference
            root_cause, confidence = calculate_causal_score(metrics)
            
            # Step 3: Publish Results
            save_to_db(service, timestamp, exit_code, root_cause, confidence, metrics)
            logger.info(f"====== RCA COMPLETED for {service} ======")
            logger.info(f"Root Cause Identified: {root_cause} (Confidence: {confidence:.2f})")
            logger.info("-" * 40)
            
        except redis.ConnectionError as e:
            logger.error(f"Lost connection to Redis: {e}")
            time.sleep(5)
            # We rely on connect_redis up higher if we needed it, but since r.brpop wraps in this loop,
            # this will just try again if connection was momentarily dropped.
            # If `r` becomes completely invalid, we'd need to re-fetch it.
            # For simplicity, we fallback to retrying brpop, which handles reconnects internally in redis-py.
        except Exception as e:
            logger.error(f"Unexpected error processing event: {e}")
            time.sleep(2)

if __name__ == "__main__":
    redis_client = connect_redis()
    process_event(redis_client)
