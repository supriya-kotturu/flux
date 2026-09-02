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
def discover(goal: str, target: str, name: str) -> None:
    """Run the LLM-driven discovery loop against a live surface and save an artifact."""
    raise click.ClickException(
        "discovery loop not implemented yet (Phase 3) — scaffolding only. "
        f"Would run goal={goal!r} target={target!r} -> artifacts/{name}.json"
    )


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
