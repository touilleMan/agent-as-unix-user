from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
import tempfile

from click.testing import CliRunner

from agent_as_another_unix_user.cli import AppState, cli
from agent_as_another_unix_user.config import load_config, save_config, Config, AgentConfig
from agent_as_another_unix_user.runner import RecordingCommandRunner


def test_new_agent_records_commands_and_writes_config() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config_path = tmp_path / "config.toml"
        home_root = tmp_path / "home"

        def handler(call):
            if call.args[:2] == ("setfacl", "--version"):
                return CompletedProcess(
                    args=list(call.args), returncode=0, stdout="setfacl 2.0", stderr=""
                )
            if call.args[:3] == ("id", "-u", "agent"):
                return CompletedProcess(
                    args=list(call.args), returncode=0, stdout="1001\n", stderr=""
                )
            return CompletedProcess(
                args=list(call.args), returncode=0, stdout="", stderr=""
            )

        runner = RecordingCommandRunner(handler=handler)
        state = AppState(
            config_path=config_path, home_root=home_root, runner=runner, is_root=True
        )

        result = CliRunner().invoke(cli, ["new", "--user", "agent", "--yes"], obj=state)
        assert result.exit_code == 0, result.output

        config = load_config(config_path)
        assert len(config.agents) == 1
        assert config.agents[0].user_name == "agent"
        assert config.agents[0].su_as_agent_group == "su-as-agent"
        assert config.agents[0].entrypoint == str(home_root / "agent" / "su_as_agent")

        assert (home_root / "agent" / "README.md").exists()
        assert (
            home_root
            / "agent"
            / ".config"
            / "agent-as-another-unix-user"
            / "su_as_agent-src"
            / "main.c"
        ).exists()
        assert any(call.args[:1] == ("groupadd",) for call in runner.calls)
        assert any(call.args[:1] == ("useradd",) for call in runner.calls)
        assert any(call.args[:1] == ("make",) for call in runner.calls)


def test_run_forwards_command_to_entrypoint() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config_path = tmp_path / "config.toml"
        home_root = tmp_path / "home"
        entrypoint = tmp_path / "su_as_agent"
        entrypoint.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        entrypoint.chmod(0o755)

        config_path.write_text(
            "[[agents]]\n"
            f'user_name = "agent"\n'
            f'su_as_agent_group = "su-as-agent"\n'
            f"entrypoint = {str(entrypoint)!r}\n",
            encoding="utf-8",
        )

        runner = RecordingCommandRunner()
        state = AppState(
            config_path=config_path, home_root=home_root, runner=runner, is_root=False
        )

        result = CliRunner().invoke(
            cli, ["run", "--user", "agent", "--", "echo", "hello"], obj=state
        )
        assert result.exit_code == 0, result.output
        assert runner.calls[-1].args == (str(entrypoint), "echo", "hello")


def test_list_marks_missing_user_broken() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config_path = tmp_path / "config.toml"
        home_root = tmp_path / "home"
        save_config(
            config_path,
            Config(agents=[
                AgentConfig(
                    user_name="ghost",
                    su_as_agent_group="su-as-ghost",
                    entrypoint=str(tmp_path / "ghost" / "su_as_agent"),
                )
            ]),
        )

        def handler(call):
            if call.args[:2] == ("setfacl", "--version"):
                return CompletedProcess(
                    args=list(call.args), returncode=0, stdout="setfacl 2.0", stderr=""
                )
            if call.args[:2] == ("id", "-nG"):
                return CompletedProcess(
                    args=list(call.args), returncode=0, stdout="su-as-ghost", stderr=""
                )
            if call.args[:3] == ("getent", "passwd", "ghost"):
                return CompletedProcess(
                    args=list(call.args), returncode=2, stdout="", stderr=""
                )
            if call.args[:3] == ("getent", "group", "su-as-ghost"):
                return CompletedProcess(
                    args=list(call.args),
                    returncode=0,
                    stdout="su-as-ghost:x:1000:\n",
                    stderr="",
                )
            return CompletedProcess(
                args=list(call.args), returncode=0, stdout="", stderr=""
            )

        runner = RecordingCommandRunner(handler=handler)
        state = AppState(
            config_path=config_path, home_root=home_root, runner=runner, is_root=False
        )

        result = CliRunner().invoke(cli, ["list"], obj=state)
        assert result.exit_code == 0, result.output
        assert "ghost: BROKEN" in result.output
        assert "missing UNIX user" in result.output
