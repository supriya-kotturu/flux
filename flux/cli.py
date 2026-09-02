"""Entry point: `flux discover ...` / `flux replay ...`.

Commands are wired up here as the real CLI surface from day one; each
subcommand's body gets filled in during the phase that builds it
(discover -> Phase 3, replay -> Phase 5) rather than the CLI itself
changing shape later.
"""

from __future__ import annotations

import click

from flux import __version__


@click.group()
@click.version_option(__version__, prog_name="flux")
def main() -> None:
    """Flux: LLM discovery -> reusable artifact -> deterministic replay."""


@main.command()
@click.option(
    "--goal",
    required=True,
    help="Natural language goal, e.g. 'look up member 12345 and read their savings balance'",
)
@click.option("--target", required=True, help="Entry point URL for the target application")
@click.option("--name", required=True, help="Name to save the resulting artifact under (artifact saving lands in Phase 4)")
@click.option("--headless/--headed", default=False, help="Headed by default — watchable, and matches the handoff design.")
@click.option("--max-steps", default=20, show_default=True)
def discover(goal: str, target: str, name: str, headless: bool, max_steps: int) -> None:
    """Run the LLM-driven discovery loop against a live surface."""
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv()

    from flux.agent.llm_client import AnthropicClient
    from flux.agent.loop import run_discovery
    from flux.observability.logger import RunLogger, new_run_id
    from flux.surface.browser import BrowserSurface

    logger = RunLogger(new_run_id("discover"), evidence_root=Path("evidence"))
    surface = BrowserSurface.launch(headless=headless)
    try:
        run = run_discovery(
            goal=goal, target=target, surface=surface,
            llm=AnthropicClient(), logger=logger, max_steps=max_steps,
        )
    finally:
        surface.close()

    click.echo(f"stop_reason={run.stop_reason} success={run.success}")
    if run.outputs:
        click.echo(f"outputs={run.outputs}")
    if run.give_up_reason:
        click.echo(f"give_up_reason={run.give_up_reason}")
    click.echo(f"(artifact recording lands in Phase 4 — artifacts/{name}.json was not written)")
    click.echo(f"evidence: {logger.run_dir}")
    if not run.success:
        raise SystemExit(1)


@main.command()
@click.option("--artifact", "artifact_name", required=True, help="Name of a saved artifact under artifacts/")
@click.option("--param", "params", multiple=True, help="key=value input parameter, repeatable")
def replay(artifact_name: str, params: tuple[str, ...]) -> None:
    """Deterministically replay a saved artifact — no LLM in the loop."""
    parsed = dict(p.split("=", 1) for p in params)
    raise click.ClickException(
        "replay engine not implemented yet (Phase 5) — scaffolding only. "
        f"Would replay artifacts/{artifact_name}.json with params={parsed}"
    )


if __name__ == "__main__":
    main()
