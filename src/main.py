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
@click.option("--endpoint", "-e", help="API endpoint URL (required unless --providers)")
@click.option("--api-key", "-k", envvar="PROXY_SLEUTH_KEY", help="API key (or set PROXY_SLEUTH_KEY env var)")
@click.option("--model", "-m", help="Claimed model name (required unless --providers)")
@click.option("--protocol", "-p", type=click.Choice(["openai", "anthropic"]), default="openai", help="API protocol")
@click.option("--mode", type=click.Choice(["quick", "standard", "full", "knowledge", "params", "context", "routing"]), default="quick", help="Detection mode")
@click.option("--output", "-o", "output_format", type=click.Choice(["term", "json", "html"]), default="term", help="Output format")
@click.option("--output-file", help="Save report to file")
@click.option("--timeout", type=float, default=120.0, help="Request timeout in seconds")
@click.option("--temperature", type=float, default=0.0, help="Sampling temperature for probes")
@click.option("--max-tokens", type=int, default=1024, help="Max tokens for probe responses")
@click.option("--providers", "providers_file", help="Path to providers.json (ccswitch-compatible config) for batch testing")
def detect(
    endpoint: Optional[str],
    api_key: Optional[str],
    model: Optional[str],
    protocol: str,
    mode: str,
    output_format: str,
    output_file: Optional[str],
    timeout: float,
    temperature: float,
    max_tokens: int,
    providers_file: Optional[str],
) -> None:
    """Run model authenticity detection against an API endpoint, or batch-test providers.

    Single endpoint:
      proxy-sleuth detect -e https://proxy.example.com/v1 -m gpt-5.6-sol -k sk-xxx

    Batch test multiple providers from a cccswitch-compatible config:
      proxy-sleuth detect --providers providers.json --mode quick
    """
    # Batch mode: test all providers from config
    if providers_file:
        _run_batch(providers_file, mode, output_format, timeout, temperature, max_tokens)
        return

    # Single endpoint mode
    if not endpoint:
        raise click.UsageError("--endpoint is required (or use --providers for batch mode)")
    if not model:
        raise click.UsageError("--model is required (or use --providers for batch mode)")
    if not api_key:
        click.echo("Error: No API key provided. Use --api-key or set PROXY_SLEUTH_KEY env var.", err=True)
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


@cccswitch.command("init")
@click.option("--output", "-o", default="./providers.json", help="Output path for template config")
def cccswitch_init(output: str) -> None:
    """Generate a template providers.json for cccswitch integration."""
    from src.utils.ccswitch import CCSwitchLoader
    path = CCSwitchLoader.init_template(output)
    click.echo(f"Template created: {path}")
    click.echo("  Edit this file to add your proxy endpoints, then run:")
    click.echo(f"  proxy-sleuth detect --providers {path} --mode quick")


@cccswitch.command("test")
@click.option("--providers", "-f", default=None, help="Path to providers.json (auto-discovered if omitted)")
@click.option("--mode", default="quick", type=click.Choice(["quick", "standard", "full"]), help="Detection mode")
@click.option("--output", "-o", "output_format", type=click.Choice(["term", "json"]), default="term")
def cccswitch_test(providers: Optional[str], mode: str, output_format: str) -> None:
    """Batch-test all providers from a cccswitch config."""
    from src.utils.ccswitch import CCSwitchLoader

    try:
        path = providers if providers else None
        config = CCSwitchLoader.load(path)
    except FileNotFoundError as e:
        click.echo(f"Error: {e}", err=True)
        click.echo("Run 'proxy-sleuth cccswitch init' to create a template.", err=True)
        sys.exit(1)

    click.echo(f"Loaded {len(config.providers)} provider(s) from {path or 'auto-discovered'}")
    _run_batch_from_config(config, mode, output_format)


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
        "statistical": 0.20,
        "capability": 0.20,
        "mixed_routing": 0.10,
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


# ── batch mode (ccswitch integration) ──────────────────────────

def _run_batch(providers_file: str, mode: str, output_format: str, timeout: float, temperature: float, max_tokens: int) -> None:
    """Load providers from file and batch-test each one."""
    from src.utils.ccswitch import CCSwitchLoader

    try:
        config = CCSwitchLoader.load(providers_file)
    except FileNotFoundError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    click.echo(f"proxy-sleuth v0.1.0 — batch mode ({len(config.providers)} providers)")
    click.echo(f"  Mode: {mode}")
    click.echo()

    _run_batch_from_config(config, mode, output_format, timeout=timeout, temperature=temperature, max_tokens=max_tokens)


def _run_batch_from_config(config, mode: str, output_format: str, **kwargs) -> None:
    """Run detection on all providers in a CCSwitchConfig."""
    results = []

    async def _batch():
        for provider in config.providers:
            if not provider.api_key:
                click.echo(f"[{provider.name}] SKIP — no API key configured")
                continue

            click.echo(f"\n{'='*60}")
            click.echo(f"[{provider.name}] Testing {provider.base_url}")
            click.echo(f"  Models: {', '.join(provider.models)}")
            click.echo(f"{'='*60}")

            for model in provider.models:
                cfg = RunConfig(
                    endpoint=provider.base_url.rstrip("/"),
                    api_key=provider.api_key,
                    model=model,
                    protocol=provider.protocol,
                    timeout=kwargs.get("timeout", 120.0),
                    temperature=kwargs.get("temperature", 0.0),
                    max_tokens=kwargs.get("max_tokens", 1024),
                    output_format=output_format,
                )
                _apply_mode_preset(cfg, mode)
                r = await _run_detection(cfg)
                results.append({"provider": provider.name, "model": model, **r})

        # Summary table
        click.echo(f"\n{'='*60}")
        click.echo("BATCH SUMMARY")
        click.echo(f"{'='*60}")
        for r in results:
            verdict = r.get("verdict", "?")
            color = "green" if verdict == "MATCH" else ("red" if verdict == "MISMATCH" else "yellow")
            score = r.get("overall_score", 0)
            click.echo(f"  [{color}]{verdict:12}[/{color}] {r['provider']:20s} {r['model']:20s} ({score:.0%})")

    asyncio.run(_batch())


if __name__ == "__main__":
    cli()
