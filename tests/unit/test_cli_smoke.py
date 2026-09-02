from click.testing import CliRunner

from flux.cli import main


def test_help_runs() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "discover" in result.output
    assert "replay" in result.output


def test_discover_and_replay_are_registered_but_not_yet_implemented() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["discover", "--goal", "g", "--target", "t", "--name", "n"])
    assert result.exit_code != 0
    assert "not implemented yet" in result.output

    result = runner.invoke(main, ["replay", "--artifact", "n"])
    assert result.exit_code != 0
    assert "not implemented yet" in result.output
