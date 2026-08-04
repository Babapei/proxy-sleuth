"""Statistical fingerprint integration — uses vendored llm-fingerprint (Node.js).

Bundled at vendor/fingerprint/ — no npm install needed.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.config import PROJECT_ROOT, RunConfig

FP_BIN = PROJECT_ROOT / "vendor" / "fingerprint" / "bin" / "fp.js"


@dataclass
class FingerprintResult:
    verdict: str
    mean_jsd: float | None = None
    details: str = ""
    raw_output: str = ""


class StatisticalFingerprinter:
    """Wraps the vendored llm-fingerprint CLI for Python.

    The vendored tool contains a database of 176 model fingerprints
    from the "One Token Is Enough" paper. No external install required.
    """

    def __init__(self, cfg: RunConfig):
        self.cfg = cfg
        self._node_available: bool | None = None
        self._tool_available: bool | None = None

    async def run(self, reference: str | None = None) -> dict[str, Any]:
        """Run statistical fingerprint verification against 176-model database.

        Uses the vendored llm-fingerprint tool (vendor/fingerprint/bin/fp.js)
        to probe the endpoint and find the closest matching model.
        """
        if not await self._ensure_tool():
            return {
                "layer": "statistical",
                "score": 0.5,
                "verdict": "NOT_AVAILABLE",
                "mean_jsd": None,
                "error": "Node.js not available. Install Node 18+ for fingerprinting.",
            }

        env = os.environ.copy()
        env["LLM_FINGERPRINT_KEY"] = self.cfg.resolve_api_key()

        try:
            # Step 1: probe the endpoint
            proc = await asyncio.create_subprocess_exec(
                "node", str(FP_BIN), "verify",
                str(self.cfg.endpoint),
                self.cfg.resolve_api_key(),
                self.cfg.model,
                "--api", "openai" if self.cfg.protocol == "openai" else "anthropic",
                "--reps", "8",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.cfg.timeout + 120,
            )
            output = stdout.decode("utf-8", errors="replace")

            result = self._parse_verify_output(output)

            return {
                "layer": "statistical",
                "score": self._score_from_verdict(result.verdict, result.mean_jsd),
                "verdict": result.verdict,
                "mean_jsd": result.mean_jsd,
                "details": result.details,
                "raw_exit_code": proc.returncode,
            }

        except asyncio.TimeoutError:
            return {
                "layer": "statistical", "score": 0.5,
                "verdict": "NOT_AVAILABLE", "mean_jsd": None,
                "error": "Fingerprinting timed out.",
            }
        except Exception as e:
            return {
                "layer": "statistical", "score": 0.5,
                "verdict": "NOT_AVAILABLE", "mean_jsd": None,
                "error": str(e),
            }

    async def match_model(self) -> dict[str, Any]:
        """Run probe + match to find the most likely model from 176 candidates."""
        if not await self._ensure_tool():
            return {"verdict": "NOT_AVAILABLE", "error": "Node.js not available."}

        env = os.environ.copy()
        env["LLM_FINGERPRINT_KEY"] = self.cfg.resolve_api_key()

        try:
            proc = await asyncio.create_subprocess_exec(
                "node", str(FP_BIN), "probe",
                str(self.cfg.endpoint),
                self.cfg.resolve_api_key(),
                self.cfg.model,
                "--api", "openai" if self.cfg.protocol == "openai" else "anthropic",
                "--reps", "8",
                "--langs", "en,zh",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.cfg.timeout + 120,
            )
            output = stdout.decode("utf-8", errors="replace")

            # Parse the probe result and then match
            return self._parse_match_output(output)

        except Exception as e:
            return {"verdict": "NOT_AVAILABLE", "error": str(e)}

    async def _ensure_tool(self) -> bool:
        if self._tool_available is not None:
            return self._tool_available

        if self._node_available is None:
            self._node_available = shutil.which("node") is not None

        if not self._node_available:
            self._tool_available = False
            return False

        self._tool_available = FP_BIN.exists()
        return self._tool_available

    def _score_from_verdict(self, verdict: str, mean_jsd: float | None) -> float:
        if verdict == "MATCH":
            return 0.85
        if verdict == "MISMATCH":
            return 0.15
        if mean_jsd is not None:
            return max(0.0, 1.0 - (mean_jsd / 0.463))
        return 0.5

    def _parse_verify_output(self, output: str) -> FingerprintResult:
        """Parse 'fp verify' output."""
        verdict_match = re.search(r'(?:verdict|Verdict)[:\s]+(\w+)', output, re.IGNORECASE)
        jsd_match = re.search(r'(?:JSD|jsd)[:\s]+([\d.]+)', output, re.IGNORECASE)

        return FingerprintResult(
            verdict=verdict_match.group(1).upper() if verdict_match else "NOT_AVAILABLE",
            mean_jsd=float(jsd_match.group(1)) if jsd_match else None,
            details=output.strip()[:500],
            raw_output=output,
        )

    def _parse_match_output(self, output: str) -> dict:
        """Parse 'fp probe' + match output to find closest model."""
        # Extract top matches from output
        matches = re.findall(r'(\S+)\s+\(JSD\s+([\d.]+)\)', output)
        top = [(name, float(jsd)) for name, jsd in matches[:5]]

        return {
            "layer": "statistical",
            "score": 0.85 if top and top[0][1] < 0.25 else 0.3,
            "verdict": "MATCH" if top and top[0][1] < 0.25 else "MISMATCH",
            "top_matches": [{"model": m, "jsd": j} for m, j in top],
            "raw": output[:500],
        }
