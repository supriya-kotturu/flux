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
    load_dotenv(".env.local", override=True)

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
        from flux.escalation.detector import discovery_should_escalate

        if discovery_should_escalate(run):
            click.echo(
                "This run stopped in a state that qualifies for human escalation "
                "(see flux.escalation.detector). Live hand-off mid-discovery isn't wired "
                "into this CLI command yet — see `flux replay --escalate-on-failure` for "
                "the real mechanism, which discovery shares the same building blocks with."
            )
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
@click.option(
    "--escalate-on-failure", is_flag=True, default=False,
    help=(
        "If replay hits a real failure (not a policy block), keep the session open, raise an "
        "intervention request with a live DevTools URL, and wait for `flux operator resume` "
        "before completing the remaining steps on that same session."
    ),
)
@click.option("--escalation-timeout", default=300.0, show_default=True, help="Seconds to wait for --escalate-on-failure.")
def replay(
    artifact_name: str, params: tuple[str, ...], secret_opts: tuple[str, ...],
    approve: bool, extra_domains: tuple[str, ...], headless: bool,
    escalate_on_failure: bool, escalation_timeout: float,
) -> None:
    """Deterministically replay a saved artifact — no LLM in the loop."""
    import os
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv()
    load_dotenv(".env.local", override=True)

    from flux.artifact import store
    from flux.escalation.detector import replay_should_escalate
    from flux.escalation.handoff import ControlPlaneStore
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

    result = run_replay(artifact, replay_params, surface, logger, approved=approve, secrets=secrets)

    if escalate_on_failure and replay_should_escalate(result):
        store_ = ControlPlaneStore()
        request = store_.raise_request(
            run_id=logger.run_id, capability=artifact.name,
            step_description=(
                artifact.steps[result.step_index].description if result.step_index is not None else artifact.checkpoint.description
            ),
            reason=f"{result.category}: {result.observed}",
            screenshot_path=result.evidence_paths.get("screenshot"),
            ax_tree_path=result.evidence_paths.get("ax_tree"),
            cdp_port=surface.cdp_port,
        )
        click.echo(f"\nEscalating — intervention request {request.id}")
        click.echo(f"  reason: {request.reason}")
        if request.devtools_url:
            click.echo(f"  take control: {request.devtools_url}")
        click.echo(f"  once you've bridged the gap, run: flux operator resume {request.id}")
        click.echo(f"  waiting up to {escalation_timeout:.0f}s...")

        try:
            store_.wait_for_resume(request.id, timeout=escalation_timeout)
        except TimeoutError as exc:
            surface.close()
            click.echo(str(exc))
            raise SystemExit(1) from exc

        resume_from = (result.step_index if result.step_index is not None else 0) + 1
        result = run_replay(
            artifact, replay_params, surface, logger, approved=approve, secrets=secrets,
            resume_from_step=resume_from,
        )

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


@main.group()
def operator() -> None:
    """List and act on pending human-escalation requests (flux.escalation)."""


@operator.command("list")
def operator_list() -> None:
    from flux.escalation.handoff import ControlPlaneStore

    pending = ControlPlaneStore().list_pending()
    if not pending:
        click.echo("No pending intervention requests.")
        return
    for request in pending:
        click.echo(f"{request.id}  [{request.status}]  {request.capability} — {request.reason}")
        click.echo(f"    stuck at: {request.step_description}")
        if request.devtools_url:
            click.echo(f"    take control: {request.devtools_url}")


@operator.command("resume")
@click.argument("request_id")
@click.option("--note", default="", help="What you did, for the evidence trail.")
def operator_resume(request_id: str, note: str) -> None:
    from flux.escalation.handoff import ControlPlaneStore

    request = ControlPlaneStore().resume(request_id, human_actions_summary=note)
    click.echo(f"{request.id} marked resumed — the waiting `flux replay --escalate-on-failure` process will pick this up.")


@main.command()
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8000, show_default=True)
def serve(host: str, port: int) -> None:
    """Run the agent-facing capability interface: saved artifacts as callable HTTP capabilities."""
    import uvicorn

    uvicorn.run("flux.api.server:app", host=host, port=port)


if __name__ == "__main__":
    main()
