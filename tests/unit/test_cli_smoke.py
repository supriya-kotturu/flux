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


def test_replay_is_registered_but_not_yet_implemented() -> None:
    result = CliRunner().invoke(main, ["replay", "--artifact", "n"])
    assert result.exit_code != 0
    assert "not implemented yet" in result.output
