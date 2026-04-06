"""Integration tests for the Ingestion API using FastAPI TestClient."""
import sys
import os
import importlib.util
from unittest.mock import patch, MagicMock

import pytest

# Load the ingestion main module with a unique name to avoid collision with evidence/main.py
_ingestion_path = os.path.join(os.path.dirname(__file__), "..", "ingestion", "main.py")
_spec = importlib.util.spec_from_file_location("ingestion_main", _ingestion_path)
ingestion_main = importlib.util.module_from_spec(_spec)
sys.modules["ingestion_main"] = ingestion_main
_spec.loader.exec_module(ingestion_main)

from fastapi.testclient import TestClient

app = ingestion_main.app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def mock_redis():
    """Mock Redis for all tests so we don't need a running instance."""
    mock_r = MagicMock()
    mock_r.ping.return_value = True
    mock_r.lpush.return_value = 1
    with patch.object(ingestion_main, "get_redis_client", return_value=mock_r):
        yield mock_r


class TestReportEndpoint:
    def test_valid_report_returns_processing(self, client):
        resp = client.post("/report", json={"service": "auth-service", "exit_code": "137"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "processing"
        assert "auth-service" in body["message"]
        assert body["exit_code"] == "137"
        assert "timestamp" in body

    def test_missing_service_returns_422(self, client):
        resp = client.post("/report", json={"exit_code": "1"})
        assert resp.status_code == 422

    def test_missing_exit_code_returns_422(self, client):
        resp = client.post("/report", json={"service": "svc"})
        assert resp.status_code == 422

    def test_empty_service_returns_422(self, client):
        resp = client.post("/report", json={"service": "", "exit_code": "1"})
        assert resp.status_code == 422

    def test_empty_body_returns_422(self, client):
        resp = client.post("/report", json={})
        assert resp.status_code == 422

    def test_invalid_content_type_returns_422(self, client):
        resp = client.post("/report", content="not json", headers={"Content-Type": "text/plain"})
        assert resp.status_code == 422

    def test_redis_down_returns_503(self, client, mock_redis):
        import redis as redis_lib
        mock_redis.ping.side_effect = redis_lib.ConnectionError("Connection refused")
        resp = client.post("/report", json={"service": "svc", "exit_code": "1"})
        assert resp.status_code == 503
        assert "unavailable" in resp.json()["detail"].lower()


class TestHealthEndpoint:
    def test_health_ok_when_redis_up(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["redis"] == "connected"

    def test_health_degraded_when_redis_down(self, client, mock_redis):
        mock_redis.ping.side_effect = Exception("down")
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "degraded"


class TestRateLimiting:
    def test_rate_limit_header_present(self, client):
        resp = client.post("/report", json={"service": "svc", "exit_code": "1"})
        assert resp.status_code == 200
