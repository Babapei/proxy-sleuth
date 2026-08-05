# FAQ

## General

**Q: How does proxy-sleuth know which model is actually behind the API?**

A: It doesn't identify with 100% certainty. It runs 7 independent detection layers and cross-verifies them. If knowledge probes + statistical fingerprint + API features all say "this is not GPT-5.6", the combined confidence is very high. No single layer is definitive on its own.

**Q: Can a sophisticated proxy bypass all 7 layers?**

A: In theory, yes. A proxy that serves the EXACT claimed model with unmodified parameters, no context truncation, and no routing — would pass everything. But such a proxy has no economic incentive to exist (it would cost the same as the official API). The proxies we're designed to catch are the ones that profit from substitution.

**Q: Does proxy-sleuth work with Claude Code / Codex CLI proxies?**

A: Yes, for OpenAI-format proxies. Anthropic-format (Claude native) support exists in the API client but hasn't been tested against live Claude Code proxies yet.

**Q: Why does the fingerprint layer show "NOT_AVAILABLE"?**

A: It requires Node.js. Install with `brew install node` or similar. The vendored copy at `vendor/fingerprint/` works without npm, but Node must be available. If Node is installed and it still fails, run `node vendor/fingerprint/bin/fp.js list` once to bootstrap the 176-model database.

## Scoring & Verdicts

**Q: What does "overall score 55%" mean?**

A: It's a weighted average of all 7 layers' individual scores, mapped to 0-100%. A high score means the layers collectively think the endpoint IS the claimed model. A low score means they think it ISN'T. 50-70% is the grey zone where re-testing or deeper analysis is recommended.

**Q: Why does the scan take so long?**

A: `--mode full` sends ~350 API requests. The knowledge probes alone are 24 questions × 3 repeats = 72 requests. The statistical fingerprint is 60-200 single-token probes. Complex capability problems can take 20+ seconds each. Use `--mode quick` for ~1 minute, `--mode full` for ~5-10 minutes.

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

A: The execution-verified benchmarks run generated code in a subprocess. Some models output malformed code or markdown wrappers that confuse the extraction. This is expected — it's part of the discrimination.

**Q: Knowledge probes show very low scores for my genuine model?**

A: Check if your model name matches (`-m gpt-5.6-sol` vs `-m gpt-5.5`). The `_should_model_know` function uses model name to determine what the model "should" know. If you claim GPT-5.5 but test gpt56_only probes, they correctly score low.
