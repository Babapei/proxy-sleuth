# FAQ

## General

**Q: How does proxy-sleuth know which model is actually behind the API?**

A: It doesn't identify with 100% certainty. It runs 7 independent detection layers and cross-verifies them. If knowledge probes + statistical fingerprint + API features all say "this is not GPT-5.6", the combined confidence is very high. No single layer is definitive on its own.

**Q: Can a sophisticated proxy bypass all 7 layers?**

A: In theory, yes. A proxy that serves the EXACT claimed model with unmodified parameters, no context truncation, and no routing — would pass everything. But such a proxy has no economic incentive to exist (it would cost the same as the official API). The proxies we're designed to catch are the ones that profit from substitution.

**Q: Does proxy-sleuth work with Claude Code / Codex CLI / Gemini CLI proxies?**

A: Yes. 7 protocols supported: `--protocol openai` (default, most common), `--protocol anthropic` (Claude), `--protocol responses` (Codex), `--protocol gemini`, `--protocol cohere`, `--protocol azure`, `--protocol ollama`. Pick the one matching the proxy's provider group.

**Q: How do I know which protocol to use?**

A: Match the proxy's provider group: Claude group → `anthropic`, Codex group → `responses`, Gemini group → `gemini`, Azure group → `azure`. Most domestic Chinese models (Kimi, Qwen, GLM, etc.) use `openai`. When in doubt, try `openai` first — it's the universal default.

**Q: Why does the fingerprint layer show "NOT_AVAILABLE"?**

A: It requires Node.js. Install with `brew install node` or similar. The vendored copy at `src/vendor/fingerprint/` (inside the package) works without npm, but Node must be available. If Node is installed and it still fails, run `node src/vendor/fingerprint/bin/fp.js list` once to bootstrap the 176-model database.

**Q: How do I identify the actual model (not just detect mismatch)?**

A: Use `--identify`: `proxy-sleuth detect -e <URL> -m <claimed> -k <key> --identify`. It matches the endpoint against the 176-model fingerprint database and reports the closest match with Jensen-Shannon divergence.

**Q: How do I list the models a proxy offers?**

A: `proxy-sleuth detect -e <URL> -k <key> --list-models` calls `GET /v1/models`. Note: many proxies hide their model list (return empty) — this is common and itself a signal.

**Q: What are baseline fingerprints for?**

A: When a new model releases but the 176-model fingerprint DB hasn't been updated, you can collect its genuine fingerprint yourself: `proxy-sleuth baseline collect -e <official-API> -m <model> -k <key>`. View collected ones with `proxy-sleuth baseline list`.

## Scoring & Verdicts

**Q: What does "overall score 55%" mean?**

A: It's a weighted average of all 7 layers' individual scores, mapped to 0-100%. A high score means the layers collectively think the endpoint IS the claimed model. A low score means they think it ISN'T. 50-70% is the grey zone where re-testing or deeper analysis is recommended.

**Q: Why does the scan take so long?**

A: `--mode full` sends ~350 API requests. Knowledge probes are 40+ questions × 3 repeats = 120+ requests. Statistical fingerprint runs 30-60 single-token probes. Complex capability problems can take 20+ seconds each. Use `--mode quick` for ~1 minute, `--mode full` for ~5-10 minutes.

**Q: Can I test multiple endpoints at once?**

A: Yes, via CC Switch integration: `proxy-sleuth cccswitch test` discovers and tests all your configured providers. Or run multiple `proxy-sleuth detect` commands in parallel.

## CC Switch

**Q: Do I need to configure anything for CC Switch integration?**

A: No. Just have CC Switch installed and configured with providers. `proxy-sleuth cccswitch test` auto-discovers them from `~/.cc-switch/cc-switch.db`.

**Q: My CC Switch version isn't v3.16.2. Will it work?**

A: Yes. The integration uses adaptive schema detection — it works across CC Switch versions with different table structures.

## Troubleshooting

**Q: "No API key provided" error?**

A: Use `-k <key>` or set `PROXY_SLEUTH_KEY` env var. CC Switch users: the key is auto-read from your CC Switch database — just use the `cccswitch test` command.

**Q: Capability layer says 0% or errors?**

A: The coding benchmarks run generated code in a subprocess sandbox (real HumanEval + MATH-500 problems). Models outputting malformed code or markdown wrappers will fail execution. This is expected discrimination — frontier models succeed, weaker ones don't.

**Q: Knowledge probes show very low scores for my genuine model?**

A: Check if your model name matches (`-m gpt-5.6-sol` vs `-m gpt-5.5`). The `_should_model_know` function uses model name to determine what the model "should" know. If you claim GPT-5.5 but test gpt56_only probes, they correctly score low.
