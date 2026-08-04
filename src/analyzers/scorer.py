"""Multi-layer scoring engine — aggregates results from all detection layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LayerScore:
    name: str
    score: float  # 0.0 ~ 1.0
    weight: float
    verdict: str  # MATCH | MISMATCH | INCONCLUSIVE | NOT_RUN
    details: dict = field(default_factory=dict)


class Scorer:
    """Aggregates per-layer results into a final verdict.

    Usage::

        scorer = Scorer()
        scorer.add("knowledge_probes", 0.85, 0.25, "MATCH")
        scorer.add("statistical", 0.72, 0.20, "MATCH")
        report = scorer.finalize()
    """

    def __init__(self):
        self.layers: list[LayerScore] = []

    def add(self, name: str, score: float, weight: float, verdict: str, details: dict | None = None) -> None:
        self.layers.append(LayerScore(
            name=name, score=score, weight=weight, verdict=verdict,
            details=details or {},
        ))

    def add_from_result(self, name: str, result: dict, weight: float) -> None:
        self.add(
            name=name,
            score=result.get("overall_score", result.get("score", 0.0)),
            weight=weight,
            verdict=result.get("verdict", "NOT_RUN"),
            details=result,
        )

    def finalize(self) -> dict[str, Any]:
        """Compute final verdict and return full report."""
        if not self.layers:
            return {"verdict": "NOT_RUN", "overall_score": 0.0, "layers": []}

        total_w = sum(l.weight for l in self.layers)
        weighted_score = sum(l.score * l.weight for l in self.layers) / total_w if total_w > 0 else 0.0

        mismatches = [l for l in self.layers if l.verdict == "MISMATCH"]

        if len(mismatches) >= 2:
            final_verdict = "MISMATCH"
        elif len(mismatches) == 1:
            final_verdict = "SUSPICIOUS"
        elif weighted_score >= 0.70:
            final_verdict = "MATCH"
        elif weighted_score <= 0.40:
            final_verdict = "MISMATCH"
        else:
            final_verdict = "INCONCLUSIVE"

        return {
            "verdict": final_verdict,
            "overall_score": round(weighted_score, 3),
            "layers": [
                {
                    "name": l.name,
                    "score": l.score,
                    "weight": l.weight,
                    "verdict": l.verdict,
                    "details": l.details,
                }
                for l in self.layers
            ],
        }
