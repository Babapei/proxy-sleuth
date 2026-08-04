"""Central configuration management."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
PROMPTS_DIR = DATA_DIR / "prompts"
BASELINES_DIR = DATA_DIR / "baselines"


@dataclass
class RunConfig:
    """Configuration for a single detection run."""

    endpoint: str = ""
    api_key: str = ""
    model: str = ""
    protocol: str = "openai"
    timeout: float = 120.0

    # Which layers to run
    run_params_integrity: bool = True
    run_context_truncation: bool = True
    run_api_features: bool = True
    run_knowledge_probes: bool = True
    run_statistical: bool = True
    run_capability: bool = True
    run_mixed_routing: bool = True

    # Probe config
    temperature: float = 0.0
    max_tokens: int = 1024

    # Output
    output_format: str = "term"  # term | json | html
    output_file: str | None = None

    def resolve_api_key(self) -> str:
        """Resolve API key from config, env vars, or keyring."""
        if self.api_key:
            return self.api_key
        for var in ("LLM_DETECT_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            val = os.environ.get(var)
            if val:
                return val
        return ""
