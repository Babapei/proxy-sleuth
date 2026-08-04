# proxy-sleuth

> Detect fake LLM APIs. Catch proxy services that substitute cheaper models.

**proxy-sleuth** is a CLI tool that verifies whether an LLM API proxy is really serving the model it claims. Multi-layer forensic analysis: knowledge boundary probes, statistical fingerprinting, parameter integrity checks, context truncation detection, and more.

## Quick Start

```bash
# Install
pip install -e .

# Quick check (knowledge probes + fingerprint + API features)
proxy-sleuth detect \
  -e https://your-proxy.example.com/v1 \
  -m gpt-5.6-sol \
  -k sk-your-key

# Full deep analysis (all 7 layers)
proxy-sleuth detect \
  -e https://your-proxy.example.com/v1 \
  -m claude-fable-5 \
  -k sk-your-key \
  --mode full

# JSON output
proxy-sleuth detect -e ... -m ... --output json --output-file report.json
```

## Detection Layers

| Layer | What it detects | Mode |
|-------|----------------|------|
| **param-integrity** | max_tokens reduction, reasoning downgrade, temperature locking, tool stripping, system prompt injection | standard+ |
| **context** | Context truncation via Needle-in-Haystack at 10/20/50/100 depths | standard+ |
| **api-features** | PTC support, Anthropic compat, tool call format, streaming, self-report | quick+ |
| **knowledge** | Knowledge boundary probes — 17 questions targeting 2026-06~07 events | quick+ |
| **fingerprint** | Statistical fingerprint (Jensen-Shannon divergence, "One Token Is Enough" method) | quick+ |
| **capability** | Coding/math/reasoning/Chinese benchmarks with auto-grading | full |
| **routing** | Mixed routing detection (alternating complexity + same-question-different-wording) | full |

### Mode presets

```bash
--mode quick      # knowledge + fingerprint + api-features + param-integrity
--mode standard   # quick + context truncation
--mode full       # all 7 layers
--mode knowledge  # knowledge probes only
--mode params     # parameter integrity only
--mode context    # context truncation only
--mode routing    # mixed routing detection only
```

## How It Works

```
Claimed model: GPT-5.6 Sol ($5/$30 per 1M tokens)
Your cost:      1/10th of official price

proxy-sleuth investigates:
  ↓
[param-integrity]  Are your request params intact?
[context]          Is your full conversation preserved?
[api-features]     Does it speak the right protocol?
[knowledge]        Does it know 2026 events it should know?
[fingerprint]      Does its random-number bias match GPT-5.6?
[capability]       Can it code, reason, and do math at GPT-5.6 level?
[routing]          Is it switching models behind your back?
  ↓
Verdict: MISMATCH — 87% probability this is actually DeepSeek V4
```

## Requirements

- Python 3.11+
- Node.js 18.17+ (optional, for statistical fingerprint layer)
- `npm install -g llm-fingerprint-detector` (optional, for statistical fingerprint)

```bash
pip install -e ".[dev]"  # includes pytest
```

## API Key

Set via `--api-key` / `-k` or the `PROXY_SLEUTH_KEY` environment variable.

## Tests

```bash
pytest tests/ -v     # 35 tests covering all detectors + scorer
```
