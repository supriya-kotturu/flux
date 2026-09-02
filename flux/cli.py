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
@click.option(
    "--allow-domain", "extra_domains", multiple=True,
    help="Additional domain the agent may navigate to, beyond --target's own host (e.g. an SSO provider). Repeatable.",
)
@click.option("--headless/--headed", default=False, help="Headed by default — watchable, and matches the handoff design.")
@click.option("--max-steps", default=20, show_default=True)
def discover(
    goal: str, target: str, name: str, params: tuple[str, ...],
    vendor_product: str | None, extra_domains: tuple[str, ...], headless: bool, max_steps: int,
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
    from flux.safety.allowlist import Allowlist
    from flux.surface.browser import BrowserSurface

    input_params = dict(p.split("=", 1) for p in params)

    # Safe by default: an agent operating against this target can't wander
    # to an arbitrary domain (a hallucinated navigate, a link on the page
    # itself) unless that domain is explicitly added.
    allowlist = Allowlist.for_domain(target, *extra_domains)

    logger = RunLogger(new_run_id("discover"), evidence_root=Path("evidence"))
    surface = BrowserSurface.launch(headless=headless, allowlist=allowlist)
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
    "--secret", "secret_opts", multiple=True,
    help=(
        "name=value for a {{secret:name}} the artifact requires (e.g. password=...), repeatable. "
        "Prefer FLUX_SECRET_<NAME> environment variables — never logged either way, but env vars "
        "don't end up in your shell history."
    ),
)
@click.option(
    "--approve", is_flag=True, default=False,
    help="Required if the artifact has any irreversible (dialog-confirmed) step — see requires_approval.",
)
@click.option(
    "--allow-domain", "extra_domains", multiple=True,
    help="Additional allowed domain beyond the artifact's own app_target host. Repeatable.",
)
@click.option("--headless/--headed", default=False)
def replay(
    artifact_name: str, params: tuple[str, ...], secret_opts: tuple[str, ...],
    approve: bool, extra_domains: tuple[str, ...], headless: bool,
) -> None:
    """Deterministically replay a saved artifact — no LLM in the loop."""
    import os
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv()

    from flux.artifact import store
    from flux.observability.logger import RunLogger, new_run_id
    from flux.replay.executor import replay as run_replay
    from flux.safety.allowlist import Allowlist
    from flux.surface.browser import BrowserSurface

    replay_params = dict(p.split("=", 1) for p in params)

    # FLUX_SECRET_PASSWORD=... -> {"password": "..."}; explicit --secret wins.
    env_secrets = {
        key[len("FLUX_SECRET_"):].lower(): value
        for key, value in os.environ.items()
        if key.startswith("FLUX_SECRET_")
    }
    secrets = {**env_secrets, **dict(p.split("=", 1) for p in secret_opts)}

    artifact = store.load(artifact_name)
    allowlist = Allowlist.for_domain(artifact.app_target.base_url, *extra_domains)
    logger = RunLogger(new_run_id("replay"), evidence_root=Path("evidence"))
    surface = BrowserSurface.launch(headless=headless, allowlist=allowlist)
    try:
        result = run_replay(artifact, replay_params, surface, logger, approved=approve, secrets=secrets)
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
