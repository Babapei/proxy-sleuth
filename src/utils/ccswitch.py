"""CCSwitch integration — auto-discover and batch-test proxy providers.

Reads a cccswitch-compatible providers config file and tests all
configured endpoints in batch mode.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.config import RunConfig


# Common locations for cccswitch / proxy provider config files
CONFIG_SEARCH_PATHS = [
    "~/.ccswitch/providers.json",
    "~/.ccswitch.json",
    "~/.config/ccswitch/providers.json",
    "~/Library/Application Support/ccswitch/providers.json",
    "./ccswitch.json",
    "./providers.json",
    "./proxy-sleuth-providers.json",
]


@dataclass
class Provider:
    name: str
    base_url: str
    api_key: str
    models: list[str] = field(default_factory=lambda: ["gpt-5.6-sol"])
    protocol: str = "openai"
    api_key_env: str = ""  # Optional: read key from env var


@dataclass
class CCSwitchConfig:
    version: str = "1.0"
    providers: list[Provider] = field(default_factory=list)


class CCSwitchLoader:
    """Loads provider configurations from cccswitch-compatible JSON files."""

    @staticmethod
    def find_config() -> Path | None:
        """Auto-discover a providers config file in common locations."""
        for path_str in CONFIG_SEARCH_PATHS:
            path = Path(path_str).expanduser().resolve()
            if path.exists():
                return path
        return None

    @staticmethod
    def load(path: str | Path | None = None) -> CCSwitchConfig:
        """Load provider config from file or discover automatically."""
        if path is None:
            path = CCSwitchLoader.find_config()
            if path is None:
                raise FileNotFoundError(
                    "No cccswitch config found. Create one with --ccswitch-init or specify path.\n"
                    f"Searched: {', '.join(CONFIG_SEARCH_PATHS)}"
                )

        with open(path) as f:
            data = json.load(f)

        config = CCSwitchConfig()
        config.version = data.get("version", "1.0")

        for p_data in data.get("providers", []):
            api_key = p_data.get("api_key", "")
            if not api_key and p_data.get("api_key_env"):
                api_key = os.environ.get(p_data["api_key_env"], "")

            config.providers.append(Provider(
                name=p_data["name"],
                base_url=p_data["base_url"],
                api_key=api_key,
                models=p_data.get("models", ["gpt-5.6-sol"]),
                protocol=p_data.get("protocol", "openai"),
                api_key_env=p_data.get("api_key_env", ""),
            ))

        return config

    @staticmethod
    def init_template(output_path: str = "./providers.json") -> Path:
        """Generate a template providers config file."""
        template = {
            "version": "1.0",
            "description": "proxy-sleuth provider config — add your proxy endpoints here",
            "providers": [
                {
                    "name": "My GPT-5.6 Proxy",
                    "base_url": "https://your-proxy.example.com/v1",
                    "api_key_env": "PROXY_KEY_1",
                    "models": ["gpt-5.6-sol"],
                    "protocol": "openai",
                },
                {
                    "name": "My Claude Proxy",
                    "base_url": "https://your-other-proxy.example.com",
                    "api_key_env": "PROXY_KEY_2",
                    "models": ["claude-fable-5"],
                    "protocol": "anthropic",
                },
            ],
        }
        out = Path(output_path).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w") as f:
            json.dump(template, f, indent=2, ensure_ascii=False)
        return out
