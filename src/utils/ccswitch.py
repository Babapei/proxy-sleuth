"""CC Switch integration — reads provider configs from CC Switch database.

Searches multiple locations and adapts to schema differences across versions.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Search paths across CC Switch versions
DB_SEARCH_PATHS = [
    Path.home() / ".cc-switch" / "cc-switch.db",
    Path.home() / ".cc-switch" / "cc-switch.sqlite",
    Path.home() / "Library" / "Application Support" / "com.ccswitch.desktop" / "cc-switch.db",
]


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
    source: str = ""


# ── public API ────────────────────────────────────────────────────

def discover_providers() -> list[Provider]:
    """Read all non-official providers from CC Switch database."""
    db_path = _find_db()
    if not db_path:
        return []

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        providers = _read_providers(conn)
        conn.close()
        return providers
    except Exception:
        return []


def get_current_provider() -> Provider | None:
    """Get the currently active provider."""
    providers = discover_providers()
    for p in providers:
        if p.is_current:
            return p
    return providers[0] if providers else None


def get_proxy_port() -> int | None:
    """Get the CC Switch local proxy port."""
    db_path = _find_db()
    if not db_path:
        return None

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        for col_name in ("listen_port", "port"):
            try:
                cur = conn.execute(
                    f"SELECT {col_name} FROM proxy_config WHERE enabled=1 ORDER BY app_type LIMIT 1"
                )
                row = cur.fetchone()
                if row and row[0]:
                    conn.close()
                    return row[0]
            except Exception:
                continue
        conn.close()
    except Exception:
        pass
    return None


# ── internal ───────────────────────────────────────────────────────

def _find_db() -> Path | None:
    for p in DB_SEARCH_PATHS:
        if p.exists():
            return p
    return None


def _read_providers(conn: sqlite3.Connection) -> list[Provider]:
    """Extract providers, adapting to schema differences across CC Switch versions."""
    table = _find_table(conn, "providers")
    if not table:
        return []

    columns = _table_columns(conn, table)

    # Build query dynamically based on available columns
    select_cols = ["name", "settings_config"]
    for col in ("id", "provider_id"):
        if col in columns:
            select_cols.insert(0, col)
    for col in ("is_current", "active"):
        if col in columns:
            select_cols.append(col)
    if "meta" in columns:
        select_cols.append("meta")
    if "app_type" in columns:
        select_cols.append("app_type")

    # Filter for custom/non-official providers
    where_parts = []
    if "category" in columns:
        where_parts.append("(category IN ('custom', 'third_party') OR category IS NULL)")
    if "app_type" in columns:
        where_parts.append("app_type IN ('codex', 'claude')")
    if "provider_type" in columns:
        where_parts.append("provider_type IS NULL OR provider_type != 'official'")

    where_clause = " AND ".join(where_parts) if where_parts else "1=1"

    try:
        cur = conn.execute(
            f"SELECT {', '.join(select_cols)} FROM {table} WHERE {where_clause}"
        )
    except Exception:
        return []

    providers: list[Provider] = []
    col_map = {name: idx for idx, name in enumerate(select_cols)}

    for row in cur.fetchall():
        name = _val(row, "name", col_map)
        config_json = _val(row, "settings_config", col_map) or "{}"
        pid = _val(row, "id", col_map) or _val(row, "provider_id", col_map) or ""
        is_current = bool(_val(row, "is_current", col_map) or _val(row, "active", col_map))
        meta_json = _val(row, "meta", col_map) or "{}"
        app_type = _val(row, "app_type", col_map) or "codex"

        try:
            config = json.loads(config_json)
            meta = json.loads(meta_json)
        except Exception:
            continue

        api_key, base_url, protocol = _extract_auth(config, meta, app_type)

        endpoints = _read_endpoints(conn, pid)

        # Extract default model from config TOML (e.g. model = "gpt-5.5")
        models = _extract_models(config, app_type)

        providers.append(Provider(
            name=name or "unknown",
            base_url=base_url or (endpoints[0] if endpoints else ""),
            api_key=api_key,
            protocol=protocol,
            provider_id=pid,
            is_current=is_current,
            endpoints=endpoints,
            models=models,
            source=str(_find_db() or ""),
        ))

    return providers


def _extract_models(config: dict, app_type: str) -> list[str]:
    """Extract default model name(s) from CC Switch config."""
    toml = config.get("config", "")
    models = re.findall(r'^model\s*=\s*"([^"]+)"', toml, re.M)
    if models:
        return models
    # Fallback: check env vars for Claude
    if app_type == "claude":
        env = config.get("env", {})
        model = env.get("ANTHROPIC_MODEL", "")
        return [model] if model else []
    return []


def _read_endpoints(conn: sqlite3.Connection, provider_id: str) -> list[str]:
    table = _find_table(conn, "provider_endpoints") or _find_table(conn, "endpoints")
    if not table or not provider_id:
        return []
    try:
        cur = conn.execute(f"SELECT url FROM {table} WHERE provider_id = ?", (provider_id,))
        return [row[0] for row in cur.fetchall()]
    except Exception:
        return []


def _extract_auth(config: dict, meta: dict, app_type: str) -> tuple[str, str, str]:
    """Extract API key, base_url, and protocol from different config formats."""
    if app_type == "claude":
        env = config.get("env", {})
        return (
            env.get("ANTHROPIC_AUTH_TOKEN", ""),
            env.get("ANTHROPIC_BASE_URL", ""),
            "anthropic",
        )

    # codex / default OpenAI format
    api_key = config.get("auth", {}).get("OPENAI_API_KEY", "")
    base_url = _extract_base_url(config.get("config", ""))
    return api_key, base_url, "openai"


def _extract_base_url(toml: str) -> str:
    match = re.search(r'base_url\s*=\s*"([^"]+)"', toml)
    if match:
        return match.group(1)
    match = re.search(r'base_url\s*=\s*(\S+)', toml)
    if match:
        return match.group(1).strip('"')
    return ""


def _find_table(conn: sqlite3.Connection, name: str) -> str | None:
    try:
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
        row = cur.fetchone()
        return row[0] if row else None
    except Exception:
        return None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        cur = conn.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in cur.fetchall()}
    except Exception:
        return set()


def _val(row: tuple, col: str, col_map: dict[str, int]) -> Any:
    idx = col_map.get(col)
    if idx is not None and idx < len(row):
        return row[idx]
    return None
