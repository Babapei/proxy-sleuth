"""Tests for the scoring engine."""

import pytest
from src.analyzers.scorer import Scorer


class TestScorer:
    def test_empty(self):
        s = Scorer()
        result = s.finalize()
        assert result["verdict"] == "NOT_RUN"
        assert result["overall_score"] == 0.0

    def test_single_layer_match(self):
        s = Scorer()
        s.add("knowledge_probes", 0.85, 0.25, "MATCH")
        result = s.finalize()
        assert result["verdict"] == "MATCH"
        assert result["overall_score"] == pytest.approx(0.85)

    def test_single_layer_mismatch(self):
        s = Scorer()
        s.add("knowledge_probes", 0.15, 0.25, "MISMATCH")
        result = s.finalize()
        # 1 mismatch → SUSPICIOUS, needs 2+ for MISMATCH
        assert result["verdict"] == "SUSPICIOUS"

    def test_multiple_layers_weighted(self):
        s = Scorer()
        s.add("a", 0.9, 0.5, "MATCH")
        s.add("b", 0.1, 0.5, "MISMATCH")
        result = s.finalize()
        assert result["overall_score"] == pytest.approx(0.5)

    def test_two_mismatches_triggers_final_mismatch(self):
        s = Scorer()
        s.add("a", 0.8, 0.3, "MATCH")
        s.add("b", 0.2, 0.3, "MISMATCH")
        s.add("c", 0.1, 0.4, "MISMATCH")
        result = s.finalize()
        assert result["verdict"] == "MISMATCH"

    def test_suspicious_one_mismatch(self):
        s = Scorer()
        s.add("a", 0.8, 0.3, "MATCH")
        s.add("b", 0.7, 0.3, "MATCH")
        s.add("c", 0.2, 0.4, "MISMATCH")
        result = s.finalize()
        assert result["verdict"] == "SUSPICIOUS"

    def test_inconclusive_mid_score(self):
        s = Scorer()
        s.add("a", 0.55, 1.0, "INCONCLUSIVE")
        result = s.finalize()
        assert result["verdict"] == "INCONCLUSIVE"

    def test_add_from_result(self):
        s = Scorer()
        s.add_from_result("test_layer", {"overall_score": 0.75, "verdict": "MATCH"}, 0.3)
        result = s.finalize()
        assert result["overall_score"] == pytest.approx(0.75)
        assert len(result["layers"]) == 1
