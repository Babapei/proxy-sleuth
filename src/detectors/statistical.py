"""Statistical fingerprint integration — vendored llm-fingerprint (Node.js).

Bundled at vendor/fingerprint/ — no npm install needed.
Falls back to globally installed 'npx llm-fingerprint' if vendor missing.
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
    """176-model behavioral fingerprint, vendored from the "One Token Is Enough" paper."""

    def __init__(self, cfg: RunConfig):
        self.cfg = cfg
        self._node: bool | None = None
        self._ready: bool | None = None

    async def run(self) -> dict[str, Any]:
        if not await self._ensure_ready():
            return {"layer": "statistical", "score": 0.5, "verdict": "NOT_AVAILABLE", "error": "Node.js not available."}

        try:
            code, out, _ = await self._fp(
                "verify", self.cfg.endpoint,
                self.cfg.resolve_api_key(), self.cfg.model,
                "--api", "openai" if self.cfg.protocol == "openai" else "anthropic",
                "--reps", "8",
            )
            r = self._parse_verify(out)
            return {"layer": "statistical", "score": self._score(r.verdict, r.mean_jsd), "verdict": r.verdict,
                    "mean_jsd": r.mean_jsd, "details": r.details, "exit_code": code}
        except asyncio.TimeoutError:
            return {"layer": "statistical", "score": 0.5, "verdict": "NOT_AVAILABLE", "error": "Timeout"}
        except Exception as e:
            return {"layer": "statistical", "score": 0.5, "verdict": "NOT_AVAILABLE", "error": str(e)}

    async def match_model(self) -> dict[str, Any]:
        if not await self._ensure_ready():
            return {"verdict": "NOT_AVAILABLE", "error": "Node.js not available."}

        try:
            code, out, _ = await self._fp(
                "probe", self.cfg.endpoint,
                self.cfg.resolve_api_key(), self.cfg.model,
                "--api", "openai" if self.cfg.protocol == "openai" else "anthropic",
                "--reps", "8", "--langs", "en,zh",
            )
            matches = re.findall(r'(\S+)\s+\(JSD\s+([\d.]+)\)', out)
            top = [{"model": m, "jsd": float(j)} for m, j in matches[:5]]
            return {"layer": "statistical",
                    "score": 0.85 if top and top[0]["jsd"] < 0.25 else 0.3,
                    "verdict": "MATCH" if top and top[0]["jsd"] < 0.25 else "MISMATCH",
                    "top_matches": top, "raw": out[:500]}
        except Exception as e:
            return {"verdict": "NOT_AVAILABLE", "error": str(e)}

    # ── internal ──────────────────────────────────────────────────

    async def _fp(self, *args: str) -> tuple[int, str, str]:
        env = {**os.environ, "LLM_FINGERPRINT_KEY": self.cfg.resolve_api_key()}
        if shutil.which("npx"):
            try:
                p = await asyncio.create_subprocess_exec("npx", "llm-fingerprint", *args,
                                                          stdout=asyncio.subprocess.PIPE,
                                                          stderr=asyncio.subprocess.PIPE, env=env)
                out, err = await asyncio.wait_for(p.communicate(), timeout=15)
                if p.returncode == 0:
                    return p.returncode, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")
            except (asyncio.TimeoutError, Exception):
                pass
        p = await asyncio.create_subprocess_exec("node", str(FP_BIN), *args,
                                                  stdout=asyncio.subprocess.PIPE,
                                                  stderr=asyncio.subprocess.PIPE, env=env)
        out, err = await asyncio.wait_for(p.communicate(), timeout=self.cfg.timeout + 120)
        return p.returncode, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")

    async def _ensure_ready(self) -> bool:
        if self._ready is not None:
            return self._ready
        if self._node is None:
            self._node = shutil.which("node") is not None
        if not self._node:
            self._ready = False; return False
        if shutil.which("npx"):
            try:
                p = await asyncio.create_subprocess_exec("npx", "llm-fingerprint", "--help",
                                                          stdout=asyncio.subprocess.DEVNULL,
                                                          stderr=asyncio.subprocess.DEVNULL)
                await asyncio.wait_for(p.communicate(), timeout=10)
                if p.returncode == 0:
                    self._ready = True; return True
            except Exception:
                pass
        self._ready = FP_BIN.exists()
        if self._ready:
            # Ensure database is bootstrapped
            try:
                p = await asyncio.create_subprocess_exec("node", str(FP_BIN), "list",
                                                          stdout=asyncio.subprocess.DEVNULL,
                                                          stderr=asyncio.subprocess.DEVNULL)
                await asyncio.wait_for(p.communicate(), timeout=15)
            except Exception:
                pass
        return self._ready

    def _score(self, verdict: str, jsd: float | None) -> float:
        if verdict == "MATCH": return 0.85
        if verdict == "MISMATCH": return 0.15
        if jsd is not None: return max(0.0, 1.0 - jsd / 0.463)
        return 0.5

    def _parse_verify(self, out: str) -> FingerprintResult:
        v = re.search(r'(?:verdict|Verdict)[:\s]+(\w+)', out, re.IGNORECASE)
        j = re.search(r'(?:JSD|jsd)[:\s]+([\d.]+)', out, re.IGNORECASE)
        return FingerprintResult(verdict=v.group(1).upper() if v else "NOT_AVAILABLE",
                                  mean_jsd=float(j.group(1)) if j else None,
                                  details=out.strip()[:500], raw_output=out)
