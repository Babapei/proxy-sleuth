# Architecture & Contributing Guide

## Project Structure

```
proxy-sleuth/
├── src/
│   ├── main.py                  # CLI entry, mode presets, layer orchestration
│   ├── config.py                # RunConfig, paths
│   ├── detectors/               # Detection layers
│   │   ├── param_integrity.py   # L0: parameter tampering
│   │   ├── context_truncation.py# L1: Needle-in-Haystack
│   │   ├── api_features.py      # L2: API parameter fingerprinting
│   │   ├── knowledge_probes.py  # L3: knowledge boundary probes
│   │   ├── statistical.py       # L4: vendored fingerprint (Node subprocess)
│   │   ├── capability.py        # L5: execution-verified benchmarks
│   │   └── mixed_routing.py     # L6: model switching detection
│   ├── analyzers/
│   │   ├── scorer.py            # Weighted multi-layer scoring
│   │   └── reporter.py          # Rich terminal output
│   └── utils/
│       ├── api_client.py        # OpenAI + Anthropic dual-protocol HTTP client
│       └── cccswitch.py         # CC Switch SQLite reader
├── data/
│   └── prompts/
│       └── knowledge_probes.json # 24 probes: timestamp + llm-verify
├── vendor/fingerprint/           # Bundled llm-fingerprint (176 models)
├── scripts/
│   └── update_api_params.py     # Auto-check API parameter freshness
├── docs/
│   ├── MAINTENANCE.md            # Data freshness guide
│   └── ARCHITECTURE.md           # This file
├── DESIGN.md                     # Complete design document
└── README.md
```

## How a Detection Run Works

```
CLI: proxy-sleuth detect -e <URL> -m <MODEL> --mode full
  ↓
main.py: _apply_mode_preset → enables layer flags
  ↓
main.py: _run_detection → runs each active layer sequentially
  ↓
Each detector: returns {"layer": "...", "score": 0.X, "verdict": "MATCH/MISMATCH", ...}
  ↓
Scorer.finalize() → weighted average + 2+ mismatch rule
  ↓
Reporter.render() → colored terminal output
```

## Adding a New Detection Layer

1. Create `src/detectors/your_layer.py` with:
```python
class YourDetector:
    def __init__(self, cfg: RunConfig):
        self.client = APIClient(...)

    async def run(self) -> dict[str, Any]:
        # Your detection logic
        return {
            "layer": "your_layer",
            "score": 0.85,
            "verdict": "MATCH",
            "your_custom_field": "value",
        }
```

2. Register in `src/main.py`:
   - Add to `WEIGHTS` dict
   - Add to `_apply_mode_preset` presets
   - Add to `_run_detection` orchestration
   - Add `--mode` choice

3. Update `src/config.py` if new RunConfig fields needed

4. Update `src/analyzers/reporter.py` for layer-specific details display

5. Add tests in `tests/`

## Design Principles

- **Each layer MUST have a valid premise in current industry conditions.** Before adding a layer, ask: does this detection signal still work in August 2026? In January 2027?
- **False positives are worse than false negatives.** A layer that cries wolf undermines the entire tool. Prefer layers with well-understood error rates (like statistical fingerprint) over heuristic ones.
- **No layer is perfect on its own.** The 7-layer framework exists because no single method catches everything. Cross-verification is the whole point.
- **Data freshness matters.** Time-sensitive layers (knowledge probes, API features, capability) degrade without maintenance. See [MAINTENANCE.md](MAINTENANCE.md).
