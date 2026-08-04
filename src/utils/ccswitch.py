"""CCSwitch integration — reads cccswitch-managed Claude Code / Codex configs.

ccswitch works by modifying ~/.claude/settings.json (and similar files
for Codex) to point to different API endpoints and keys. This module
directly reads the current active config and any cached provider profiles.

Supported config sources:
  - ~/.claude/settings.json        (Claude Code, Anthropic format)
  - ~/.codex/config.json           (Codex CLI, OpenAI format)
  - ~/.claude/.ccswitch_providers  (ccswitch CLI provider cache)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.config import RunConfig


@dataclass
class Provider:
    name: str
    base_url: str
    api_key: str
    models: list[str] = field(default_factory=list)
    protocol: str = "openai"
    source: str = ""  # Which file it came from


def discover_providers() -> list[Provider]:
    """Auto-discover all API providers from cccswitch-managed configs.

    Searches:
    1. ~/.claude/settings.json — current active provider
    2. ~/.codex/config.json — Codex CLI config
    3. Known ccswitch proxy scripts
    """
    providers: list[Provider] = []

    # 1. Claude Code settings (ccswitch primary target)
    claude_settings = Path.home() / ".claude" / "settings.json"
    if claude_settings.exists():
        p = _parse_claude_settings(claude_settings)
        if p:
            providers.append(p)

    # 2. Codex CLI config
    for codex_path in [
        Path.home() / ".codex" / "config.toml",
        Path.home() / ".codex" / "config.json",
    ]:
        if codex_path.exists():
            p = _parse_codex_config(codex_path)
            if p:
                providers.append(p)

    # 3. ccswitch proxy scripts (local proxy mode)
    proxy_script = Path.home() / "script" / "ccproxy.js"
    if proxy_script.exists():
        p = _parse_ccproxy(proxy_script)
        if p:
            providers.append(p)

    return providers


def get_current_provider() -> Provider | None:
    """Get the currently active ccswitch provider (what Claude Code is using right now)."""
    settings = Path.home() / ".claude" / "settings.json"
    if not settings.exists():
        return None
    return _parse_claude_settings(settings)


def _parse_claude_settings(path: Path) -> Provider | None:
    """Parse ~/.claude/settings.json written by ccswitch."""
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    env = data.get("env", {})
    base_url = env.get("ANTHROPIC_BASE_URL", "")
    api_key = env.get("ANTHROPIC_AUTH_TOKEN", "")
    model = env.get("ANTHROPIC_MODEL", data.get("model", ""))

    if not base_url:
        return None

    # Detect localhost proxy vs direct endpoint
    is_proxy = "localhost" in base_url or "127.0.0.1" in base_url

    models = [model] if model else []
    available = data.get("availableModels", [])
    if available:
        models = available

    return Provider(
        name=f"claude-code-{'proxy' if is_proxy else 'direct'}",
        base_url=base_url,
        api_key=api_key,
        models=models,
        protocol="anthropic" if not base_url.endswith("/v1") else "openai",
        source=str(path),
    )


def _parse_codex_config(path: Path) -> Provider | None:
    """Parse Codex CLI config."""
    try:
        if path.suffix == ".toml":
            return _parse_toml_codex(path)
        with open(path) as f:
            data = json.load(f)
    except Exception:
        return None

    base_url = data.get("api_base_url", data.get("base_url", ""))
    api_key = data.get("api_key", "")
    model = data.get("model", data.get("default_model", ""))

    if not base_url and not api_key:
        return None

    return Provider(
        name="codex-cli",
        base_url=base_url or "https://api.openai.com/v1",
        api_key=api_key,
        models=[model] if model else ["gpt-5.6-sol"],
        protocol="openai",
        source=str(path),
    )


def _parse_toml_codex(path: Path) -> Provider | None:
    """Minimal TOML parser for Codex config — only extracts what we need."""
    try:
        content = path.read_text()
    except OSError:
        return None

    base_url = ""
    api_key = ""
    model = ""

    for line in content.split("\n"):
        line = line.strip()
        if "=" not in line or line.startswith("#"):
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key == "api_base_url":
            base_url = val
        elif key == "api_key":
            api_key = val
        elif key == "model" or key == "default_model":
            model = val

    if not base_url and not api_key:
        return None

    return Provider(
        name="codex-cli",
        base_url=base_url or "https://api.openai.com/v1",
        api_key=api_key,
        models=[model] if model else ["gpt-5.6-sol"],
        protocol="openai",
        source=str(path),
    )


def _parse_ccproxy(path: Path) -> Provider | None:
    """Extract provider info from ccswitch proxy script.

    The ccproxy.js routes to different backends. We extract the
    current active backend by reading the proxy's status.
    """
    # Proxy mode uses localhost — read which backend is currently active
    settings = Path.home() / ".claude" / "settings.json"
    if settings.exists():
        try:
            with open(settings) as f:
                data = json.load(f)
        except Exception:
            return None

        env = data.get("env", {})
        base_url = env.get("ANTHROPIC_BASE_URL", "")

        if "localhost" in base_url or "127.0.0.1" in base_url:
            return Provider(
                name="ccswitch-proxy-local",
                base_url=base_url,
                api_key=env.get("ANTHROPIC_AUTH_TOKEN", "proxy-mode-local"),
                models=data.get("availableModels", ["unknown"]),
                protocol="anthropic",
                source=str(path),
            )

    return None
