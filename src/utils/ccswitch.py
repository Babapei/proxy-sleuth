"""CC Switch integration — reads provider configs from ~/.cc-switch/cc-switch.db.

CC Switch stores all provider configs in a SQLite database:
  ~/.cc-switch/cc-switch.db
  - providers: each provider's name, API key, TOML config, endpoint
  - provider_endpoints: backup/multiple endpoint URLs per provider
  - proxy_config: local proxy settings (port, timeouts, failover)
  - settings.json: current active provider ID

The CC Switch runs a local proxy on 127.0.0.1:PORT (default 15721)
that routes to the selected provider with automatic failover.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Provider:
    name: str
    base_url: str
    api_key: str
    models: list[str] = field(default_factory=list)
    protocol: str = "openai"
    provider_id: str = ""
    is_current: bool = False
    endpoints: list[str] = field(default_factory=list)
    source: str = "cc-switch.db"


# ── public API ────────────────────────────────────────────────────

def discover_providers() -> list[Provider]:
    """Read all non-official providers from CC Switch database."""
    db_path = _db_path()
    if not db_path.exists():
        return []

    conn = sqlite3.connect(str(db_path))
    try:
        return _read_providers(conn)
    finally:
        conn.close()


def get_current_provider() -> Provider | None:
    """Get the currently active provider from CC Switch database."""
    providers = discover_providers()
    for p in providers:
        if p.is_current:
            return p
    return providers[0] if providers else None


def get_proxy_port() -> int | None:
    """Get the CC Switch local proxy port from the database."""
    db_path = _db_path()
    if not db_path.exists():
        return None

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "SELECT listen_port FROM proxy_config WHERE app_type='codex' AND enabled=1"
        )
        row = cur.fetchone()
        return row[0] if row else None
    except Exception:
        return None
    finally:
        conn.close()


# ── internal ───────────────────────────────────────────────────────

def _db_path() -> Path:
    return Path.home() / ".cc-switch" / "cc-switch.db"


def _read_providers(conn: sqlite3.Connection) -> list[Provider]:
    """Extract custom (non-official) providers from the database.

    Supports both Codex (OpenAI-format) and Claude (Anthropic-format) providers.
    """
    cur = conn.execute(
        "SELECT id, app_type, name, settings_config, is_current, meta "
        "FROM providers "
        "WHERE (app_type IN ('codex', 'claude')) "
        "  AND (category IN ('custom', 'third_party') OR category IS NULL)"
    )
    providers: list[Provider] = []

    for row in cur.fetchall():
        pid, app_type, name, config_json, is_current, meta_json = row

        config = json.loads(config_json) if config_json else {}
        meta = json.loads(meta_json) if meta_json else {}

        if app_type == "codex":
            api_key = config.get("auth", {}).get("OPENAI_API_KEY", "")
            base_url = _extract_base_url(config.get("config", ""))
            protocol = "openai"
        elif app_type == "claude":
            env = config.get("env", {})
            api_key = env.get("ANTHROPIC_AUTH_TOKEN", "")
            base_url = env.get("ANTHROPIC_BASE_URL", "")
            protocol = "anthropic"
        else:
            continue

        endpoints = _read_endpoints(conn, pid)

        providers.append(Provider(
            name=name,
            base_url=base_url or (endpoints[0] if endpoints else ""),
            api_key=api_key,
            protocol=protocol,
            provider_id=pid,
            is_current=bool(is_current),
            endpoints=endpoints,
            source=str(_db_path()),
        ))

    return providers


def _read_endpoints(conn: sqlite3.Connection, provider_id: str) -> list[str]:
    cur = conn.execute(
        "SELECT url FROM provider_endpoints WHERE provider_id = ?", (provider_id,)
    )
    return [row[0] for row in cur.fetchall()]


def _extract_base_url(toml: str) -> str:
    """Extract base_url from CC Switch's stored TOML config block.

    Format example:
        [model_providers.codex]
        base_url = "https://lingsuan.top"
    """
    match = re.search(r'base_url\s*=\s*"([^"]+)"', toml)
    if match:
        return match.group(1)

    # Try without quotes
    match = re.search(r'base_url\s*=\s*(\S+)', toml)
    if match:
        return match.group(1).strip('"')

    return ""
