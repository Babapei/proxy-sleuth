"""Auto-update scripts for proxy-sleuth data freshness.

Usage:
  python3 scripts/update_api_params.py     # refresh parameter fingerprint data
  python3 scripts/check_new_models.py      # check for new model releases
"""

from __future__ import annotations

import json
import sys
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = Path.home() / ".proxy-sleuth" / "api_params_cache.json"

# Parameters known to be model-specific fingerprints
FINGERPRINT_PARAMS = [
    "min_p", "top_a", "include_reasoning", "repetition_penalty",
    "frequency_penalty", "logit_bias", "reasoning_effort",
]

# Models to track for API parameter changes
TRACKED_FAMILIES = {
    "openai/gpt-5.6-sol": "gpt",
    "deepseek/deepseek-v4-flash-0731": "deepseek",
    "deepseek/deepseek-v4-pro": "deepseek",
    "qwen/qwen3.8-max": "qwen",
    "anthropic/claude-fable-5": "claude",
    "anthropic/claude-opus-5": "claude",
    "moonshotai/kimi-k3": "kimi",
    "glm/glm-5.2": "glm",
    "z-ai/glm-5.2": "glm",
    "x-ai/grok-4.5": "grok",
    "minimax/minimax-m3": "minimax",
}


async def fetch_current_params() -> dict:
    """Fetch current model parameter data from OpenRouter API."""
    print("[api_params] Fetching model data from OpenRouter...")
    params_data = {}

    async with httpx.AsyncClient(timeout=60) as client:
        # Fetch ALL models from OpenRouter
        resp = await client.get("https://openrouter.ai/api/v1/models")
        if resp.status_code != 200:
            print(f"  ERROR: OpenRouter returned {resp.status_code}")
            return params_data

        all_models = resp.json().get("data", [])
        print(f"  Got {len(all_models)} models from OpenRouter")

        # Look up our tracked models by ID
        model_lookup = {m["id"]: m for m in all_models}

        for model_id, family in TRACKED_FAMILIES.items():
            model_info = model_lookup.get(model_id)
            if model_info is None:
                # Try partial match
                for m_id, m_info in model_lookup.items():
                    if model_id.replace("-", "") in m_id.replace("-", ""):
                        model_info = m_info
                        break

            if model_info:
                supported = model_info.get("supported_parameters", [])
                fingerprints = {p: p in supported for p in FINGERPRINT_PARAMS}
                params_data[model_id] = {
                    "family": family,
                    "fingerprints": fingerprints,
                    "all_params": supported,
                    "context_length": model_info.get("context_length"),
                    "pricing": model_info.get("pricing", {}),
                    "updated": datetime.now(timezone.utc).isoformat(),
                }
                print(f"  [{family:10s}] {model_id}: {sum(fingerprints.values())}/{len(FINGERPRINT_PARAMS)} fingerprint params")
            else:
                print(f"  [{family:10s}] {model_id}: NOT FOUND in OpenRouter")

    return params_data


def analyze_changes(old_data: dict, new_data: dict) -> list[str]:
    """Compare old and new parameter data, return human-readable diff."""
    changes = []

    for model_id, new_info in new_data.items():
        old_info = old_data.get(model_id)
        if old_info is None:
            changes.append(f"[NEW MODEL] {model_id} ({new_info['family']}) — needs investigation")
            continue

        old_fp = old_info.get("fingerprints", {})
        new_fp = new_info.get("fingerprints", {})

        added = [p for p in new_fp if new_fp[p] and not old_fp.get(p, False)]
        removed = [p for p in new_fp if not new_fp[p] and old_fp.get(p, False)]

        if added:
            changes.append(f"[{model_id}] NEW params: {', '.join(added)}")
        if removed:
            changes.append(f"[{model_id}] REMOVED params: {', '.join(removed)}")

    # Check for removed models
    for model_id in old_data:
        if model_id not in new_data:
            changes.append(f"[REMOVED] {model_id} no longer in API")

    return changes


def generate_probe_updates(changes: list[str]) -> str:
    """Generate human-readable probe update suggestions from param changes."""
    if not changes:
        return "No parameter changes detected. Fingerprint data is current."

    lines = ["## Parameter Fingerprint Changes Detected\n", f"Checked at: {datetime.now(timezone.utc).isoformat()}\n"]
    lines.extend(f"- {c}" for c in changes)
    lines.append("\n### Suggested Actions")

    for c in changes:
        if "min_p" in c or "top_a" in c:
            lines.append(f"- {c} → Update api_features.py model_hint for affected checks")
        if "reasoning_effort" in c:
            lines.append(f"- {c} → reasoning_effort is no longer GPT-exclusive, consider removing from detection")
        if "NEW MODEL" in c:
            lines.append(f"- {c} → Add new knowledge probe group targeting this model's release events")

    return "\n".join(lines)


async def main():
    """Main entry point for auto-update."""
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"

    new_data = await fetch_current_params()

    old_data = {}
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE) as f:
                old_data = json.load(f)
        except Exception:
            pass

    if mode == "check":
        changes = analyze_changes(old_data, new_data)
        report = generate_probe_updates(changes)
        print(f"\n{report}")
        if changes:
            print(f"\nSaved current snapshot to {DATA_FILE}")
            with open(DATA_FILE, "w") as f:
                json.dump(new_data, f, indent=2, ensure_ascii=False)
    elif mode == "update":
        with open(DATA_FILE, "w") as f:
            json.dump(new_data, f, indent=2, ensure_ascii=False)
        print(f"Updated {DATA_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
