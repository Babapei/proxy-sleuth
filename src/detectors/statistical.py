"""Statistical fingerprint integration — Python wrapper for llm-fingerprint-detector.

Delegates to the Node.js CLI tool (ToseaAI/llm-fingerprint-detector) via
subprocess. Falls back gracefully if Node.js is not available.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.config import PROJECT_ROOT, RunConfig


@dataclass
class FingerprintResult:
    verdict: str  # "MATCH" | "MISMATCH" | "UNCERTAIN" | "NOT_AVAILABLE"
    mean_jsd: float | None = None
    details: str = ""
    raw_output: str = ""


class StatisticalFingerprinter:
    """Wraps llm-fingerprint-detector (Node.js CLI) for Python."""

    # Bundled reference models available in llm-fingerprint-detector
    BUNDLED_REFS = [
        "openai/gpt-4o",
        "openai/gpt-4o-mini",
        "openai/gpt-4.1-mini",
        "anthropic/claude-sonnet-4.5",
        "google/gemini-2.5-flash",
        "deepseek/deepseek-chat",
        "meta/llama-3.1-8b",
        "qwen/qwen3-30b-a3b",
        "mistral/mistral-small-3.2",
        "glm/glm-4.5",
        "moonshotai/kimi-k2",
    ]

    def __init__(self, cfg: RunConfig):
        self.cfg = cfg
        self._node_available: bool | None = None
        self._tool_available: bool | None = None

    async def run(self, reference: str | None = None) -> dict[str, Any]:
        """Run statistical fingerprint verification.

        Args:
            reference: Path to reference fingerprint JSON, or name of bundled
                      reference (e.g. 'openai/gpt-4o-mini'). If None, tries
                      to auto-select based on claimed model.
        """
        if not await self._ensure_tool():
            return {
                "layer": "statistical",
                "score": 0.5,
                "verdict": "NOT_AVAILABLE",
                "mean_jsd": None,
                "error": "llm-fingerprint-detector not available. Install with: npm install -g llm-fingerprint-detector",
            }

        ref = reference or self._guess_reference()

        env = os.environ.copy()
        env["LLM_FINGERPRINT_API_KEY"] = self.cfg.resolve_api_key()

        try:
            proc = await asyncio.create_subprocess_exec(
                "npx", "llm-fingerprint-detector", "verify",
                "--base-url", self.cfg.endpoint,
                "--model", self.cfg.model,
                "--reference", ref,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.cfg.timeout + 60,
            )
            output = stdout.decode("utf-8", errors="replace")

            result = self._parse_output(output)

            return {
                "layer": "statistical",
                "score": self._score_from_verdict(result.verdict, result.mean_jsd),
                "verdict": result.verdict,
                "mean_jsd": result.mean_jsd,
                "details": result.details,
                "reference": ref,
                "raw_exit_code": proc.returncode,
            }

        except asyncio.TimeoutError:
            return {
                "layer": "statistical",
                "score": 0.5,
                "verdict": "NOT_AVAILABLE",
                "mean_jsd": None,
                "error": "Fingerprinting timed out.",
            }
        except Exception as e:
            return {
                "layer": "statistical",
                "score": 0.5,
                "verdict": "NOT_AVAILABLE",
                "mean_jsd": None,
                "error": str(e),
            }

    async def collect_reference(self, output_path: str | None = None) -> dict[str, Any]:
        """Collect a new reference fingerprint from the current endpoint."""
        if not await self._ensure_tool():
            return {"error": "llm-fingerprint-detector not available.", "verdict": "NOT_AVAILABLE"}

        out_path = output_path or str(PROJECT_ROOT / "data" / "baselines" / f"{self.cfg.model.replace('/', '-')}.json")
        env = os.environ.copy()
        env["LLM_FINGERPRINT_API_KEY"] = self.cfg.resolve_api_key()

        try:
            proc = await asyncio.create_subprocess_exec(
                "npx", "llm-fingerprint-detector", "fingerprint",
                "--base-url", self.cfg.endpoint,
                "--model", self.cfg.model,
                "--out", out_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            await asyncio.wait_for(proc.communicate(), timeout=self.cfg.timeout + 60)
            return {"verdict": "COLLECTED", "path": out_path}
        except Exception as e:
            return {"error": str(e), "verdict": "FAILED"}

    async def _ensure_tool(self) -> bool:
        """Check if Node.js and llm-fingerprint-detector are available."""
        if self._tool_available is not None:
            return self._tool_available

        if self._node_available is None:
            self._node_available = shutil.which("node") is not None

        if not self._node_available:
            self._tool_available = False
            return False

        try:
            proc = await asyncio.create_subprocess_exec(
                "npx", "llm-fingerprint-detector", "--help",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.communicate(), timeout=15)
            self._tool_available = proc.returncode == 0
        except Exception:
            self._tool_available = False

        return self._tool_available

    def _guess_reference(self) -> str:
        """Map claimed model to bundled reference name."""
        model = self.cfg.model.lower()

        mappings = [
            (["gpt-5.6", "gpt5.6"], "openai/gpt-4o"),
            (["gpt-4o-mini"], "openai/gpt-4o-mini"),
            (["gpt-4.1-mini"], "openai/gpt-4.1-mini"),
            (["gpt-4o"], "openai/gpt-4o"),
            (["claude", "fable", "sonnet", "opus", "mythos"], "anthropic/claude-sonnet-4.5"),
            (["deepseek"], "deepseek/deepseek-chat"),
            (["llama"], "meta/llama-3.1-8b"),
            (["qwen"], "qwen/qwen3-30b-a3b"),
            (["gemini"], "google/gemini-2.5-flash"),
        ]

        for keys, ref in mappings:
            if any(k in model for k in keys):
                return ref

        return "openai/gpt-4o"  # Best-effort default

    def _score_from_verdict(self, verdict: str, mean_jsd: float | None) -> float:
        """Map fingerprint verdict to 0-1 score."""
        if verdict == "MATCH":
            return 0.85
        if verdict == "MISMATCH":
            return 0.15
        if verdict == "UNCERTAIN":
            return 0.5
        if mean_jsd is not None:
            # Linear interpolation: 0 JSD → 1.0, 0.463 (different model) → 0.0
            return max(0.0, 1.0 - (mean_jsd / 0.463))
        return 0.5

    def _parse_output(self, output: str) -> FingerprintResult:
        """Parse llm-fingerprint-detector CLI output."""
        # Try matching known patterns
        verdict_match = re.search(r'Verdict:\s*(\w+)', output)
        jsd_match = re.search(r'Mean JSD:\s*([\d.]+)', output)

        if not verdict_match:
            # Maybe exit code based? Try harder
            if "MATCH" in output.upper():
                verdict = "MATCH"
            elif "MISMATCH" in output.upper():
                verdict = "MISMATCH"
            elif "UNCERTAIN" in output.upper():
                verdict = "UNCERTAIN"
            else:
                return FingerprintResult(verdict="NOT_AVAILABLE", details=output[:500])

            return FingerprintResult(
                verdict=verdict,
                mean_jsd=float(jsd_match.group(1)) if jsd_match else None,
                details=output.strip()[:500],
            )

        return FingerprintResult(
            verdict=verdict_match.group(1),
            mean_jsd=float(jsd_match.group(1)) if jsd_match else None,
            details=output.strip()[:500],
            raw_output=output,
        )
