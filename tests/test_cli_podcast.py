from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from distill.cli import app

runner = CliRunner()


def test_cli_selects_podcastfy_without_mutating_config(tmp_db, tmp_path):
    config = {
        "database": {"path": str(tmp_db.db_path)},
        "output": {"dir": str(tmp_path)},
        "podcast": {"provider": "gemini-api-tts"},
    }
    with (
        patch("distill.cli.load_config", return_value=config),
        patch(
            "distill.outputs.podcast.generate_podcast", new_callable=AsyncMock, return_value=None
        ) as generate,
    ):
        result = runner.invoke(
            app,
            [
                "podcast",
                "--provider",
                "podcastfy-edge",
                "--script-provider",
                "gemini",
            ],
        )
    assert result.exit_code == 0, result.output
    selected = generate.await_args.args[1]["podcast"]
    assert selected["provider"] == "podcastfy-edge"
    assert selected["podcastfy"]["script_provider"] == "gemini"
    assert config["podcast"] == {"provider": "gemini-api-tts"}


def test_cli_rejects_unknown_provider_before_generation():
    with (
        patch("distill.cli.load_config", return_value={}),
        patch("distill.outputs.podcast.generate_podcast", new_callable=AsyncMock) as generate,
    ):
        result = runner.invoke(app, ["podcast", "--provider", "notebooklm"])
    assert result.exit_code != 0
    generate.assert_not_called()


def test_setup_installs_into_configured_isolated_environment(tmp_path):
    interpreter = tmp_path / "optional-env/bin/python"
    config = {"podcast": {"podcastfy": {"python": str(interpreter)}}}
    with (
        patch("distill.cli.load_config", return_value=config),
        patch("distill.cli.shutil.which", return_value="/usr/bin/uv"),
        patch("distill.cli.subprocess.run") as run,
    ):
        result = runner.invoke(app, ["podcast-setup"])
    assert result.exit_code == 0, result.output
    commands = [call.args[0] for call in run.call_args_list]
    assert commands[0] == [
        "/usr/bin/uv",
        "venv",
        str(interpreter.parent.parent),
        "--python",
        "3.12",
    ]
    assert commands[1][:5] == ["/usr/bin/uv", "pip", "install", "--python", str(interpreter)]
    assert "podcastfy==0.4.3" in commands[1]
    assert commands[2][0:2] == [str(interpreter), "-I"]
    assert commands[2][-1] == "--setup"
