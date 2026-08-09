"""CLI entry point for proxy-sleuth."""

from __future__ import annotations

import asyncio
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
@click.option("--model", "-m", required=True, help="Claimed model name (e.g. gpt-5.6-sol, claude-fable-5)")
@click.option("--protocol", "-p", type=click.Choice(["openai", "anthropic", "responses", "gemini"]), default="openai", help="API protocol")
@click.option("--mode", type=click.Choice(["quick", "standard", "full", "knowledge", "params", "context", "routing", "features", "fingerprint", "capability"]), default="quick", help="Detection mode")
@click.option("--output", "-o", "output_format", type=click.Choice(["term", "json", "html"]), default="term", help="Output format")
@click.option("--output-file", help="Save report to file")
@click.option("--timeout", type=float, default=120.0, help="Request timeout in seconds")
@click.option("--temperature", type=float, default=0.0, help="Sampling temperature for probes")
@click.option("--max-tokens", type=int, default=1024, help="Max tokens for probe responses")
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
) -> None:
    """Run model authenticity detection against an API endpoint.

    For cccswitch users: run 'proxy-sleuth cccswitch test' to
    auto-discover and test your currently active provider.
    """
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
def baseline_collect(endpoint: str, api_key: str, model: str, protocol: str) -> None:
    """Collect a baseline fingerprint from a trusted endpoint."""
    click.echo(f"Collecting baseline for {model} from {endpoint} ...")
    click.echo("(Not yet implemented — coming in Phase 3)")


@baseline.command("list")
def baseline_list() -> None:
    """List available baseline fingerprints."""
    click.echo("Available baselines:")
    click.echo("  (No baselines collected yet. Run 'proxy-sleuth baseline collect' first.)")


@cli.group()
def cccswitch() -> None:
    """Manage cccswitch-compatible provider configs."""


@cli.group()
def cccswitch() -> None:
    """Auto-detect and test providers configured via cccswitch.

    Reads directly from ~/.claude/settings.json and related config
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
    from src.utils.ccswitch import discover_providers, get_current_provider

    providers = discover_providers()

    if not providers:
        click.echo("No cccswitch-managed configs found.")
        click.echo("  Looked for: ~/.claude/settings.json, ~/.codex/config.*")
        click.echo("  Make sure cccswitch is installed and configured first.")
        sys.exit(1)

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
