"""CLI entry point for proxy-sleuth."""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Optional

import click

from src.config import RunConfig


@click.group()
@click.version_option(version="0.1.0", prog_name="proxy-sleuth")
def cli() -> None:
    """proxy-sleuth — Detect fake LLM APIs.

    Verify whether an LLM API proxy is really serving the model it claims.
    Multi-layer forensic analysis: knowledge probes, statistical
    fingerprinting, parameter integrity, context truncation, and more.
    """


@cli.command()
@click.option("--endpoint", "-e", required=True, help="API endpoint URL (e.g. https://proxy.example.com/v1)")
@click.option("--api-key", "-k", envvar="PROXY_SLEUTH_KEY", help="API key (or set PROXY_SLEUTH_KEY env var)")
@click.option("--model", "-m", required=False, help="Claimed model name (e.g. gpt-5.6-sol, claude-fable-5)")
@click.option("--protocol", "-p", type=click.Choice(["openai", "anthropic", "responses", "gemini", "cohere", "azure", "ollama"]), default="openai", help="API protocol")
@click.option("--mode", type=click.Choice(["quick", "standard", "full", "knowledge", "params", "context", "routing", "features", "fingerprint", "capability"]), default="quick", help="Detection mode")
@click.option("--output", "-o", "output_format", type=click.Choice(["term", "json", "html"]), default="term", help="Output format")
@click.option("--output-file", help="Save report to file")
@click.option("--timeout", type=float, default=120.0, help="Request timeout in seconds")
@click.option("--temperature", type=float, default=0.0, help="Sampling temperature for probes")
@click.option("--max-tokens", type=int, default=1024, help="Max tokens for probe responses")
@click.option("--list-models", is_flag=True, help="List available models from the endpoint (GET /v1/models) instead of detecting")
@click.option("--identify", is_flag=True, help="Identify which model this endpoint actually serves (matches against 176-model fingerprint DB)")
def detect(
    endpoint: str,
    api_key: Optional[str],
    model: str,
    protocol: str,
    mode: str,
    output_format: str,
    output_file: Optional[str],
    timeout: float,
    temperature: float,
    max_tokens: int,
    list_models: bool,
    identify: bool,
) -> None:
    """Run model authenticity detection against an API endpoint.

    For cccswitch users: run 'proxy-sleuth cccswitch test' to
    auto-discover and test your currently active provider.
    """
    # Identify mode
    if identify:
        if not model:
            click.echo("Error: --identify requires -m <model> (the model name to probe as).", err=True)
            sys.exit(1)
        key = api_key or os.environ.get("PROXY_SLEUTH_KEY", "")
        if not key:
            click.echo("Error: No API key provided.", err=True)
            sys.exit(1)
        cfg = RunConfig(endpoint=endpoint.rstrip("/"), api_key=key, model=model, protocol=protocol, timeout=timeout)
        asyncio.run(_run_identify(cfg))
        return

    # List models mode
    if list_models:
        asyncio.run(_list_models(endpoint, api_key, protocol, timeout))
        return

    if not model:
        click.echo("Error: --model is required (or use --list-models).", err=True)
        sys.exit(1)

    if not api_key:
        click.echo("Error: No API key provided. Use --api-key or set PROXY_SLEUTH_KEY env var.", err=True)
        click.echo("Tip: if using cccswitch, try 'proxy-sleuth cccswitch test' instead.", err=True)
        sys.exit(1)

    cfg = RunConfig(
        endpoint=endpoint.rstrip("/"),
        api_key=api_key,
        model=model,
        protocol=protocol,
        timeout=timeout,
        temperature=temperature,
        max_tokens=max_tokens,
        output_format=output_format,
        output_file=output_file,
    )

    _apply_mode_preset(cfg, mode)

    click.echo(f"proxy-sleuth v0.1.0 — investigating {endpoint}")
    click.echo(f"  Claimed model: {model}")
    click.echo(f"  Mode: {mode} ({' + '.join(_active_layers(cfg))})")
    click.echo()

    asyncio.run(_run_detection(cfg))


@cli.group()
def baseline() -> None:
    """Manage model baseline fingerprints."""


@baseline.command("collect")
@click.option("--endpoint", "-e", required=True)
@click.option("--api-key", "-k", required=True, envvar="PROXY_SLEUTH_KEY")
@click.option("--model", "-m", required=True)
@click.option("--protocol", "-p", type=click.Choice(["openai", "anthropic"]), default="openai")
@click.option("--reps", type=int, default=16, help="Sampling repetitions (higher = more accurate, slower)")
def baseline_collect(endpoint: str, api_key: str, model: str, protocol: str, reps: int) -> None:
    """Collect a baseline fingerprint from a trusted endpoint.

    Runs the statistical fingerprint probe against a known-genuine
    endpoint and saves the resulting fingerprint to data/baselines/.
    Use this when a new model is released but the fingerprint DB
    hasn't been updated yet.
    """
    from src.utils.api_client import APIClient, Protocol
    from src.utils.ccswitch import Provider
    import src.detectors.statistical as stat

    click.echo(f"Collecting baseline for {model} from {endpoint} ...")

    async def _collect():
        fp = stat.StatisticalFingerprinter.__new__(stat.StatisticalFingerprinter)
        fp.cfg = RunConfig(endpoint=endpoint, api_key=api_key, model=model, protocol=protocol)
        fp._node = None
        fp._ready = None
        if not await fp._ensure_ready():
            click.echo("Error: Node.js not available for fingerprinting.", err=True)
            return 1

        code, out, err = await fp._fp(
            "probe", endpoint, api_key, model,
            "--api", "openai" if protocol == "openai" else "anthropic",
            "--reps", str(reps),
        )
        if code != 0:
            click.echo(f"Probe failed: {err[:300] if err else out[:300]}", err=True)
            return 1

        # fp probe saves result.json to its user data dir; parse the path
        import re as _re
        m = _re.search(r"Saved to (.+?\.json)", out)
        if not m:
            click.echo("Probe ran but could not locate saved result.json.", err=True)
            click.echo(out[-500:])
            return 1

        src_path = m.group(1).strip()
        from pathlib import Path
        from src.config import BASELINES_DIR
        BASELINES_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = model.replace("/", "-").replace(":", "-")
        dest = BASELINES_DIR / f"{safe_name}.json"
        Path(src_path).replace(dest)
        click.echo(f"Baseline saved: {dest}")
        return 0

    import sys as _sys
    _sys.exit(asyncio.run(_collect()))


@baseline.command("list")
def baseline_list() -> None:
    """List available baseline fingerprints."""
    from src.config import BASELINES_DIR
    files = sorted(BASELINES_DIR.glob("*.json")) if BASELINES_DIR.exists() else []
    if not files:
        click.echo("No baselines collected yet.")
        click.echo("Run: proxy-sleuth baseline collect -e <official-api> -m <model> -k <key>")
        return
    click.echo("Available baselines:")
    for f in files:
        size = f.stat().st_size
        click.echo(f"  {f.stem}  ({size} bytes)")


@cli.group()
def cccswitch() -> None:
    """Auto-detect and test providers configured via cccswitch.

    Reads directly from ~/.cc-switch/cc-switch.db and related config
    files that cccswitch manages. No manual config needed.
    """


@cccswitch.command("test")
@click.option("--mode", default="quick", type=click.Choice(["quick", "standard", "full", "knowledge", "params", "context", "routing", "features", "fingerprint", "capability"]), help="Detection mode")
@click.option("--output", "-o", "output_format", type=click.Choice(["term", "json"]), default="term")
def cccswitch_test(mode: str, output_format: str) -> None:
    """Auto-discover and test the currently active cccswitch provider.

    Reads ~/.claude/settings.json to find what API endpoint Claude Code
    is currently pointed at, then runs detection against it.
    """
    from src.utils.ccswitch import discover_providers, get_current_provider, get_proxy_port

    providers = discover_providers()

    if not providers:
        click.echo("No cccswitch-managed configs found.")
        click.echo("  Looked for: ~/.cc-switch/cc-switch.db and related paths.")
        click.echo("  Make sure cccswitch is installed and configured first.")
        sys.exit(1)

    port = get_proxy_port()
    if port:
        click.echo(f"CC Switch local proxy: 127.0.0.1:{port}")

    current = get_current_provider()
    click.echo(f"Found {len(providers)} cccswitch-managed provider(s):")
    for p in providers:
        marker = " ← CURRENT" if current and p.source == current.source else ""
        click.echo(f"  [{p.name}] {p.base_url} ({len(p.models)} models){marker}")
    click.echo()

    for provider in providers:
        if not provider.api_key:
            click.echo(f"[{provider.name}] SKIP — no API key in config")
            continue

        click.echo(f"\n{'='*60}")
        click.echo(f"[{provider.name}] Testing {provider.base_url}")
        if provider.models:
            click.echo(f"  Models: {', '.join(provider.models)}")
        click.echo(f"{'='*60}")

        for model in (provider.models or ["unknown"]):
            cfg = RunConfig(
                endpoint=provider.base_url.rstrip("/"),
                api_key=provider.api_key,
                model=model,
                protocol=provider.protocol,
                output_format=output_format,
            )
            _apply_mode_preset(cfg, mode)
            asyncio.run(_run_detection(cfg))

    click.echo(f"\n{'='*60}")
    click.echo("Done. All cccswitch providers tested.")
    click.echo(f"{'='*60}")


async def _run_identify(cfg: RunConfig) -> None:
    """Identify which model an endpoint serves via fingerprint matching."""
    from src.detectors.statistical import StatisticalFingerprinter

    click.echo(f"Identifying model behind {cfg.endpoint} ...")
    click.echo(f"  Probed as: {cfg.model}")
    click.echo()

    fp = StatisticalFingerprinter(cfg)
    result = await fp.match_model()

    if result.get("verdict") == "NOT_AVAILABLE":
        click.echo(f"Error: {result.get('error', 'fingerprinting unavailable')}", err=True)
        return

    top = result.get("top_matches", [])
    if not top:
        click.echo("No close matches found in the 176-model database.")
        return

    click.echo("Top matches (lower JSD = closer):")
    click.echo(f"  {'Model':<40s} {'JSD':>8s}")
    click.echo(f"  {'-'*40} {'-'*8}")
    for m in top:
        click.echo(f"  {m['model']:<40s} {m['jsd']:>8.4f}")

    best = top[0]
    click.echo()
    if best["jsd"] < 0.25:
        click.echo(f"[green]Strong match: this endpoint likely serves {best['model']}[/green]")
    elif best["jsd"] < 0.35:
        click.echo(f"[yellow]Uncertain: closest is {best['model']} but not a strong match[/yellow]")
    else:
        click.echo(f"[red]No close match — this model is not in the 176-model database[/red]")


async def _list_models(endpoint: str, api_key: Optional[str], protocol: str, timeout: float) -> None:
    from src.utils.api_client import APIClient

    key = api_key or os.environ.get("PROXY_SLEUTH_KEY", "")
    client = APIClient(endpoint, key, protocol=protocol, timeout=timeout)
    models = await client.list_models()

    if not models:
        click.echo("No models returned. The endpoint may not support /v1/models listing.")
        click.echo("(This is common for proxy services that hide their model list.)")
        return

    click.echo(f"Found {len(models)} model(s):")
    for m in models:
        click.echo(f"  {m}")


def _apply_mode_preset(cfg: RunConfig, mode: str) -> None:
    """Enable/disable detection layers based on mode."""
    presets = {
        "quick":    [True, False, True, True, True, False, False],
        "standard": [True, True,  True, True, True, False,  False],
        "full":     [True, True,  True, True, True, True,   True],
        "knowledge":[False, False, False, True, False, False, False],
        "params":   [True, False, False, False, False, False, False],
        "context":  [False, True, False, False, False, False, False],
        "routing":  [False, False, False, False, False, False, True],
        "features": [False, False, True, False, False, False, False],
        "fingerprint": [False, False, False, False, True, False, False],
        "capability":  [False, False, False, False, False, True, False],
    }
    flags = presets.get(mode, presets["quick"])
    (cfg.run_params_integrity, cfg.run_context_truncation, cfg.run_api_features,
     cfg.run_knowledge_probes, cfg.run_statistical, cfg.run_capability,
     cfg.run_mixed_routing) = flags


def _active_layers(cfg: RunConfig) -> list[str]:
    """Return list of active detection layer names."""
    layers = []
    if cfg.run_params_integrity:   layers.append("param-integrity")
    if cfg.run_context_truncation: layers.append("context")
    if cfg.run_api_features:       layers.append("api-features")
    if cfg.run_knowledge_probes:   layers.append("knowledge")
    if cfg.run_statistical:        layers.append("fingerprint")
    if cfg.run_capability:         layers.append("capability")
    if cfg.run_mixed_routing:      layers.append("routing")
    return layers


async def _run_detection(cfg: RunConfig) -> None:
    """Orchestrate the detection pipeline — run all active layers."""
    from src.analyzers.scorer import Scorer
    scorer = Scorer()
    results: list[dict] = []

    # Layer weights (from DESIGN.md)
    WEIGHTS = {
        "param_integrity": 0.05,
        "context_truncation": 0.10,
        "api_features": 0.10,
        "knowledge_probes": 0.25,
        "statistical": 0.25,       # up: unaffected by capability convergence
        "capability": 0.10,        # down: models converging rapidly
        "mixed_routing": 0.15,     # up: more common with hybrid proxies
    }

    # ── Layer 0: Parameter integrity ──
    if cfg.run_params_integrity:
        from src.detectors.param_integrity import ParamIntegrityDetector
        click.echo("[param-integrity] Checking request parameter tampering...")
        detector = ParamIntegrityDetector(cfg)
        r = await detector.run()
        scorer.add_from_result("param_integrity", r, WEIGHTS["param_integrity"])
        results.append(r)

    # ── Layer 1: Context truncation ──
    if cfg.run_context_truncation:
        from src.detectors.context_truncation import ContextTruncationDetector
        click.echo("[context] Running Needle-in-Haystack tests...")
        detector = ContextTruncationDetector(cfg)
        r = await detector.run()
        scorer.add_from_result("context_truncation", r, WEIGHTS["context_truncation"])
        results.append(r)

    # ── Layer 2: API features ──
    if cfg.run_api_features:
        from src.detectors.api_features import APIFeaturesDetector
        click.echo("[api-features] Probing API-level characteristics...")
        detector = APIFeaturesDetector(cfg)
        r = await detector.run()
        scorer.add_from_result("api_features", r, WEIGHTS["api_features"])
        results.append(r)

    # ── Layer 3: Knowledge probes ──
    if cfg.run_knowledge_probes:
        from src.detectors.knowledge_probes import KnowledgeProbeEngine
        click.echo("[knowledge] Running knowledge boundary probes...")
        engine = KnowledgeProbeEngine(cfg)
        r = await engine.run()
        scorer.add_from_result("knowledge_probes", r, WEIGHTS["knowledge_probes"])
        results.append(r)

    # ── Layer 4: Statistical fingerprint ──
    if cfg.run_statistical:
        from src.detectors.statistical import StatisticalFingerprinter
        click.echo("[fingerprint] Running statistical fingerprint (single-token distributions)...")
        fingerprinter = StatisticalFingerprinter(cfg)
        r = await fingerprinter.run()
        scorer.add_from_result("statistical", r, WEIGHTS["statistical"])
        results.append(r)

    # ── Layer 5: Capability benchmark ──
    if cfg.run_capability:
        from src.detectors.capability import CapabilityDetector
        click.echo("[capability] Running reasoning/coding/math/Chinese benchmarks...")
        detector = CapabilityDetector(cfg)
        r = await detector.run()
        scorer.add_from_result("capability", r, WEIGHTS["capability"])
        results.append(r)

    # ── Layer 6: Mixed routing ──
    if cfg.run_mixed_routing:
        from src.detectors.mixed_routing import MixedRoutingDetector
        click.echo("[routing] Testing for mixed model routing...")
        detector = MixedRoutingDetector(cfg)
        r = await detector.run()
        scorer.add_from_result("mixed_routing", r, WEIGHTS["mixed_routing"])
        results.append(r)

    if not results:
        click.echo("No detection layers enabled for this mode.")
        return

    # ── Final score ──
    final = scorer.finalize()
    final["layers"] = results
    _render_result(final, cfg)


def _render_result(result, cfg: RunConfig) -> None:
    """Render detection results."""
    if cfg.output_format == "json":
        import json
        output = json.dumps(result, indent=2, ensure_ascii=False)
        if cfg.output_file:
            with open(cfg.output_file, "w") as f:
                f.write(output)
        click.echo(output)
    else:
        from src.analyzers.reporter import TerminalReporter
        reporter = TerminalReporter()
        reporter.render(result)


if __name__ == "__main__":
    cli()
