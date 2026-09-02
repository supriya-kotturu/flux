from click.testing import CliRunner

from flux.cli import main


def test_help_runs() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "discover" in result.output
    assert "replay" in result.output


def test_discover_help_shows_its_options() -> None:
    # discover now runs a real browser + LLM loop (Phase 3) - not something a
    # fast unit test should invoke. The loop itself is covered against a
    # scripted fake LLM in tests/integration/test_discovery_loop.py.
    result = CliRunner().invoke(main, ["discover", "--help"])
    assert result.exit_code == 0
    assert "--goal" in result.output
    assert "--target" in result.output


def test_replay_help_shows_its_options() -> None:
    # replay now runs a real browser + the deterministic executor (Phase 5) -
    # covered against the live mock bank in tests/integration/test_replay_executor.py.
    result = CliRunner().invoke(main, ["replay", "--help"])
    assert result.exit_code == 0
    assert "--artifact" in result.output
    assert "--approve" in result.output
