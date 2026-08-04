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
        layer = result.get("layer", "unknown")
        if layer == "knowledge_probes":
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
