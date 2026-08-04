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
@click.option("--protocol", "-p", type=click.Choice(["openai", "anthropic"]), default="openai", help="API protocol")
@click.option("--mode", type=click.Choice(["quick", "standard", "full", "knowledge", "params", "context", "routing"]), default="quick", help="Detection mode")
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
    """Run model authenticity detection against an API endpoint."""
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

    # Apply mode presets
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
    """Orchestrate the detection pipeline."""
    # TODO: Phase 1 — knowledge probes only for now
    # Future phases will add the other layers

    if cfg.run_knowledge_probes:
        from src.detectors.knowledge_probes import KnowledgeProbeEngine
        engine = KnowledgeProbeEngine(cfg)
        result = await engine.run()
        _render_result(result, cfg)
    else:
        click.echo("No detection layers enabled for this mode.")


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
