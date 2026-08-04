"""Terminal reporter — renders detection results with colored output."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

console = Console()


class TerminalReporter:
    """Renders detection results to the terminal using Rich."""

    def render(self, result: dict[str, Any]) -> None:
        # Multi-layer consolidated report
        if "layers" in result:
            self._render_consolidated(result)
        elif result.get("layer") == "knowledge_probes":
            self._render_knowledge(result)
        else:
            console.print_json(data=result)

    # ── knowledge probes ────────────────────────────────────────

    def _render_knowledge(self, result: dict) -> None:
        verdict = result["verdict"]
        color = {"MATCH": "green", "MISMATCH": "red", "INCONCLUSIVE": "yellow"}.get(verdict, "white")
        console.print()
        console.print(Panel.fit(
            f"[bold {color}]Verdict: {verdict}[/bold {color}]  |  "
            f"Score: {result['overall_score']:.0%}  |  "
            f"Model: {result['claimed_model']}  |  "
            f"Requests: {result['total_requests']}  |  "
            f"Duration: {result['total_duration_ms']:.0f}ms",
            title="[bold]Knowledge Boundary Probes[/bold]",
            border_style=color,
        ))
        console.print()

        for group in result["groups"]:
            exp_label = "EXPECTED" if group["expected"] else "REFERENCE"
            exp_color = "green" if group["expected"] else "dim"

            table = Table(
                title=f"[bold]{group['group']}[/bold] — {group['description']}  [{exp_color}]{exp_label}[/{exp_color}]",
                box=box.SIMPLE_HEAVY,
            )
            table.add_column("Probe", style="dim", width=22)
            table.add_column("Score", justify="center", width=8)
            table.add_column("Keywords", width=28)
            table.add_column("Response Preview", width=42)

            for probe in group["probes"]:
                s = probe["score"]
                s_color = "green" if s >= 0.7 else ("yellow" if s >= 0.4 else "red")
                if probe.get("error"):
                    s_color = "red"

                kw = ", ".join(probe.get("keywords_matched", [])) or "—"
                if probe.get("error"):
                    kw = f"[red]ERR: {probe['error'][:25]}[/red]"

                snippet = (probe.get("response_snippet", "") or "")[:80].replace("\n", " ")
                if not snippet and not probe.get("error"):
                    snippet = "[dim](empty)[/dim]"

                table.add_row(probe["id"], f"[{s_color}]{s:.0%}[/{s_color}]", kw, snippet)

            gs = group["score"]
            gc = "green" if gs >= 0.7 else ("yellow" if gs >= 0.4 else "red")
            table.caption = f"Group: [{gc}]{gs:.0%}[/{gc}]"
            console.print(table)
            console.print()

        if verdict == "MISMATCH":
            console.print("[bold red]⚠ Knowledge boundary mismatch — model may be substituted.[/bold red]")
        elif verdict == "MATCH":
            console.print("[bold green]✓ Knowledge boundary test passed.[/bold green]")
        else:
            console.print("[bold yellow]? Inconclusive — try --mode full for deeper analysis.[/bold yellow]")
        console.print()

    # ── consolidated multi-layer ─────────────────────────────────

    def _render_consolidated(self, result: dict) -> None:
        verdict = result.get("verdict", "UNKNOWN")
        score = result.get("overall_score", 0)
        color = {"MATCH": "green", "MISMATCH": "red", "SUSPICIOUS": "yellow", "INCONCLUSIVE": "yellow"}.get(verdict, "white")

        console.print()
        console.print(Panel.fit(
            f"[bold {color}]Verdict: {verdict}[/bold {color}]  |  "
            f"Overall Score: {score:.0%}",
            title="[bold]proxy-sleuth Detection Report[/bold]",
            border_style=color,
        ))
        console.print()

        # Per-layer summary table
        table = Table(title="Layer Results", box=box.SIMPLE_HEAVY)
        table.add_column("Layer", style="bold", width=20)
        table.add_column("Score", justify="center", width=10)
        table.add_column("Verdict", justify="center", width=14)
        table.add_column("Details", width=46)

        for layer in result.get("layers", []):
            lv = layer.get("verdict", "NOT_RUN")
            ls = layer.get("overall_score", layer.get("score", 0))
            lc = "green" if lv == "MATCH" else ("red" if lv == "MISMATCH" else "yellow")

            detail = ""
            if layer.get("layer") == "param_integrity":
                failed = layer.get("failed_count", 0)
                detail = f"{failed} check(s) failed" if failed else "All checks passed"
            elif layer.get("layer") == "context_truncation":
                if layer.get("truncated"):
                    detail = f"Truncated at ~{layer.get('estimated_context_rounds', '?')} rounds"
                else:
                    detail = "Full context preserved"
            elif layer.get("layer") == "api_features":
                detail = f"Best guess: {layer.get('best_model_guess', 'unknown')}"
            elif layer.get("layer") == "knowledge_probes":
                detail = f"Groups: {len(layer.get('groups', []))}"

            table.add_row(layer.get("layer", "unknown"), f"[{lc}]{ls:.0%}[/{lc}]", f"[{lc}]{lv}[/{lc}]", detail)

        console.print(table)
        console.print()

        # Red flags
        mismatched = [l for l in result.get("layers", []) if l.get("verdict") == "MISMATCH"]
        if mismatched:
            console.print(Panel.fit(
                "\n".join(f"• [{l.get('layer', '?')}] {l.get('verdict', '')}" for l in mismatched),
                title="[bold red]Red Flags[/bold red]",
                border_style="red",
            ))
            console.print()

        if verdict == "MISMATCH":
            console.print("[bold red]⚠ MULTIPLE LAYERS DETECT MISMATCH — high confidence of model substitution.[/bold red]")
        elif verdict == "SUSPICIOUS":
            console.print("[bold yellow]⚠ Some layers show anomalies — investigate further with --mode full.[/bold yellow]")
        elif verdict == "MATCH":
            console.print("[bold green]✓ All tested layers match the claimed model.[/bold green]")
        console.print()
