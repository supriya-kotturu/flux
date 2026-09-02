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
@click.option("--name", required=True, help="Name to save the resulting artifact under")
@click.option(
    "--param", "params", multiple=True,
    help="name=concrete_value used this run, e.g. member_id=10001 — templated into {{name}} when recorded",
)
@click.option("--vendor-product", default=None, help="Tag for the underlying app template (multi-tenant reuse story)")
@click.option("--headless/--headed", default=False, help="Headed by default — watchable, and matches the handoff design.")
@click.option("--max-steps", default=20, show_default=True)
def discover(
    goal: str, target: str, name: str, params: tuple[str, ...],
    vendor_product: str | None, headless: bool, max_steps: int,
) -> None:
    """Run the LLM-driven discovery loop against a live surface and save the resulting artifact."""
    from pathlib import Path
    from urllib.parse import urlsplit

    from dotenv import load_dotenv

    load_dotenv()

    from flux.agent.llm_client import AnthropicClient
    from flux.agent.loop import run_discovery
    from flux.artifact import store
    from flux.artifact.recorder import record
    from flux.observability.logger import RunLogger, new_run_id
    from flux.surface.browser import BrowserSurface

    input_params = dict(p.split("=", 1) for p in params)

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
    click.echo(f"evidence: {logger.run_dir}")

    if not run.success:
        raise SystemExit(1)

    origin = urlsplit(target)
    artifact = record(
        run, name=name, description=goal,
        base_url=f"{origin.scheme}://{origin.netloc}", vendor_product=vendor_product,
        input_params=input_params,
    )
    path = store.save(artifact)
    click.echo(f"artifact saved: {path} (requires_approval={artifact.requires_approval})")


@main.command()
@click.option("--artifact", "artifact_name", required=True, help="Name of a saved artifact under artifacts/")
@click.option("--param", "params", multiple=True, help="name=value input parameter, repeatable")
@click.option(
    "--approve", is_flag=True, default=False,
    help="Required if the artifact has any irreversible (dialog-confirmed) step — see requires_approval.",
)
@click.option("--headless/--headed", default=False)
def replay(artifact_name: str, params: tuple[str, ...], approve: bool, headless: bool) -> None:
    """Deterministically replay a saved artifact — no LLM in the loop."""
    from pathlib import Path

    from flux.artifact import store
    from flux.observability.logger import RunLogger, new_run_id
    from flux.replay.executor import replay as run_replay
    from flux.surface.browser import BrowserSurface

    replay_params = dict(p.split("=", 1) for p in params)
    artifact = store.load(artifact_name)
    logger = RunLogger(new_run_id("replay"), evidence_root=Path("evidence"))
    surface = BrowserSurface.launch(headless=headless)
    try:
        result = run_replay(artifact, replay_params, surface, logger, approved=approve)
    finally:
        surface.close()

    click.echo(f"result={result.kind}")
    if result.kind == "success":
        click.echo(f"outputs={result.outputs}")
    elif result.kind == "business_outcome":
        click.echo(f"outcome={result.name}: {result.description}")
    else:
        click.echo(f"category={result.category} step_index={result.step_index}")
        click.echo(f"expected: {result.expected}")
        click.echo(f"observed: {result.observed}")
        if result.detail:
            click.echo(f"detail: {result.detail}")
    click.echo(f"evidence: {logger.run_dir}")

    if result.kind == "failure":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
