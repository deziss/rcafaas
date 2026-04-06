"""Integration tests for the Evidence API using FastAPI TestClient."""
import sys
import os
import importlib.util
from unittest.mock import patch, MagicMock
from datetime import datetime

import pytest

_evidence_path = os.path.join(os.path.dirname(__file__), "..", "evidence", "main.py")
_spec = importlib.util.spec_from_file_location("evidence_main", _evidence_path)
evidence_main = importlib.util.module_from_spec(_spec)
sys.modules["evidence_main"] = evidence_main
_spec.loader.exec_module(evidence_main)

from fastapi.testclient import TestClient

app = evidence_main.app


@pytest.fixture
def client():
    return TestClient(app)


def _make_mock_row():
    return {
        "id": 1,
        "service_name": "auth-service",
        "incident_time": datetime(2026, 1, 1, 12, 0, 0),
        "exit_code": "137",
        "root_cause": "CPU Starvation",
        "confidence_score": 0.85,
        "cpu_usage": 92.5,
        "memory_usage": 45.0,
        "disk_io": 12.3,
        "network_drops": 3,
        "evidence_logs": "ERROR: process killed",
        "analyzed_at": datetime(2026, 1, 1, 12, 1, 0),
    }


@pytest.fixture(autouse=True)
def mock_db_pool():
    """Mock the database pool for all tests."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [_make_mock_row()]
    mock_conn.cursor.return_value = mock_cursor

    with patch.object(evidence_main, "get_db_connection", return_value=mock_conn), \
         patch.object(evidence_main, "return_db_connection"):
        yield mock_conn, mock_cursor


class TestReportsEndpoint:
    def test_get_reports_default(self, client):
        resp = client.get("/reports")
        assert resp.status_code == 200
        body = resp.json()
        assert "reports" in body
        assert len(body["reports"]) == 1
        assert body["reports"][0]["service_name"] == "auth-service"
        # datetime should be serialized to ISO string
        assert isinstance(body["reports"][0]["incident_time"], str)

    def test_get_reports_with_service_filter(self, client, mock_db_pool):
        resp = client.get("/reports", params={"service": "auth-service"})
        assert resp.status_code == 200
        _, mock_cursor = mock_db_pool
        # Verify the query included the service filter
        call_args = mock_cursor.execute.call_args
        assert "WHERE service_name" in call_args[0][0]

    def test_get_reports_with_limit(self, client, mock_db_pool):
        resp = client.get("/reports", params={"limit": 5})
        assert resp.status_code == 200
        _, mock_cursor = mock_db_pool
        call_args = mock_cursor.execute.call_args
        assert 5 in call_args[0][1]  # limit param passed to query

    def test_limit_too_high_returns_422(self, client):
        resp = client.get("/reports", params={"limit": 200})
        assert resp.status_code == 422

    def test_limit_zero_returns_422(self, client):
        resp = client.get("/reports", params={"limit": 0})
        assert resp.status_code == 422

    def test_limit_negative_returns_422(self, client):
        resp = client.get("/reports", params={"limit": -1})
        assert resp.status_code == 422

    def test_db_unavailable_returns_503(self, client):
        with patch.object(evidence_main, "get_db_connection", return_value=None):
            resp = client.get("/reports")
            assert resp.status_code == 503
            assert "unavailable" in resp.json()["detail"].lower()

    def test_reports_contain_explicit_columns(self, client):
        resp = client.get("/reports")
        report = resp.json()["reports"][0]
        expected_fields = {
            "id", "service_name", "incident_time", "exit_code",
            "root_cause", "confidence_score", "cpu_usage", "memory_usage",
            "disk_io", "network_drops", "evidence_logs", "analyzed_at"
        }
        assert expected_fields.issubset(set(report.keys()))


class TestHealthEndpoint:
    def test_health_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["database"] == "connected"

    def test_health_degraded_when_db_down(self, client):
        with patch.object(evidence_main, "get_db_connection", return_value=None):
            resp = client.get("/health")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "degraded"
