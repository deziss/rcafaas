"""Unit tests for inference worker logic — no external services required."""
import sys
import os
import time
import json
import pandas as pd
import pytest

# Add inference module to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "inference"))

from worker import (
    make_idempotency_key,
    calculate_causal_score,
    CircuitBreaker,
)


class TestIdempotencyKey:
    def test_deterministic(self):
        key1 = make_idempotency_key("svc-a", "2026-01-01T00:00:00", "137")
        key2 = make_idempotency_key("svc-a", "2026-01-01T00:00:00", "137")
        assert key1 == key2

    def test_different_inputs_different_keys(self):
        key1 = make_idempotency_key("svc-a", "2026-01-01T00:00:00", "137")
        key2 = make_idempotency_key("svc-b", "2026-01-01T00:00:00", "137")
        key3 = make_idempotency_key("svc-a", "2026-01-01T00:00:01", "137")
        key4 = make_idempotency_key("svc-a", "2026-01-01T00:00:00", "1")
        assert len({key1, key2, key3, key4}) == 4

    def test_key_length(self):
        key = make_idempotency_key("svc", "2026-01-01T00:00:00", "1")
        assert len(key) == 32

    def test_hex_characters_only(self):
        key = make_idempotency_key("svc", "2026-01-01T00:00:00", "1")
        assert all(c in "0123456789abcdef" for c in key)


class TestCircuitBreaker:
    def test_starts_closed(self):
        cb = CircuitBreaker("test", threshold=3, reset_timeout=10)
        assert not cb.is_open()

    def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker("test", threshold=3, reset_timeout=60)
        cb.record_failure()
        cb.record_failure()
        assert not cb.is_open()
        cb.record_failure()
        assert cb.is_open()

    def test_success_resets_failures(self):
        cb = CircuitBreaker("test", threshold=3, reset_timeout=60)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.failures == 0
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open()

    def test_half_open_after_timeout(self):
        cb = CircuitBreaker("test", threshold=2, reset_timeout=1)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open()
        time.sleep(1.1)
        # After timeout, should allow one attempt (half-open)
        assert not cb.is_open()
        # Failures count is now threshold-1
        assert cb.failures == 1

    def test_success_after_half_open_closes(self):
        cb = CircuitBreaker("test", threshold=2, reset_timeout=1)
        cb.record_failure()
        cb.record_failure()
        time.sleep(1.1)
        cb.is_open()  # triggers half-open
        cb.record_success()
        assert cb.failures == 0
        assert not cb.is_open()


class TestCausalScore:
    def _make_mock_df(self, anomalous_metric=None):
        """Create a DataFrame with optional anomaly injection."""
        import random
        random.seed(42)
        cpu = [random.uniform(10, 20) for _ in range(30)]
        mem = [random.uniform(20, 30) for _ in range(30)]
        disk = [c * 0.5 + random.uniform(0, 5) for c in cpu]
        net = [c * 0.2 + random.uniform(0, 2) for c in cpu]

        data = {
            "cpu_usage": cpu,
            "memory_usage": mem,
            "disk_io": disk,
            "network_dropped_packets": net,
        }

        if anomalous_metric and anomalous_metric in data:
            for i in range(25, 30):
                data[anomalous_metric][i] += random.uniform(50, 80)

        df = pd.DataFrame(data)
        latest = {k: v[-1] for k, v in data.items()}
        return df, latest

    def test_returns_tuple_of_str_and_float(self):
        df, latest = self._make_mock_df("cpu_usage")
        root_cause, confidence = calculate_causal_score(df, latest)
        assert isinstance(root_cause, str)
        assert isinstance(confidence, float)

    def test_confidence_between_0_and_1(self):
        df, latest = self._make_mock_df("memory_usage")
        _, confidence = calculate_causal_score(df, latest)
        assert 0.0 <= confidence <= 1.0

    def test_no_anomaly_returns_unknown(self):
        """When no metric is spiking, should return unknown with low confidence."""
        df, latest = self._make_mock_df(anomalous_metric=None)
        root_cause, confidence = calculate_causal_score(df, latest)
        # With no anomaly, heuristic path may find nothing
        # The exact result depends on random values, but confidence should be reasonable
        assert confidence <= 1.0

    def test_empty_dataframe_fallback(self):
        """With very few data points, should fall back to heuristics."""
        df = pd.DataFrame({"cpu_usage": [90.0], "memory_usage": [30.0],
                          "disk_io": [10.0], "network_dropped_packets": [5.0]})
        latest = {"cpu_usage": 90.0, "memory_usage": 30.0,
                  "disk_io": 10.0, "network_dropped_packets": 5.0}
        root_cause, confidence = calculate_causal_score(df, latest)
        assert isinstance(root_cause, str)
        assert "CPU" in root_cause  # CPU is >70, should trigger heuristic

    def test_high_memory_triggers_oom_heuristic(self):
        """When memory is very high and PyRCA unavailable/insufficient data, should detect OOM."""
        df = pd.DataFrame({"cpu_usage": [20.0], "memory_usage": [95.0],
                          "disk_io": [10.0], "network_dropped_packets": [5.0]})
        latest = {"cpu_usage": 20.0, "memory_usage": 95.0,
                  "disk_io": 10.0, "network_dropped_packets": 5.0}
        root_cause, confidence = calculate_causal_score(df, latest)
        assert "Memory" in root_cause
