from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
import typing

from click.testing import CliRunner
import pytest

from agent_as_another_unix_user.cli import AppState, cli
from agent_as_another_unix_user.config import (
    Config,
)
from agent_as_another_unix_user.runner import RecordingCommandRunner

from tests.conftest import load_config


@pytest.fixture
def state(tmp_path: Path) -> typing.Generator[AppState, None, None]:
    config_path = tmp_path / "config.toml"
    with Config.open(config_path) as config:
        yield AppState(
            config_path=config_path,
            config=config,
            home_root=tmp_path / "home",
            runner=RecordingCommandRunner(),
            is_root=True,
        )


def test_new_agent_records_commands_and_writes_config(state: AppState) -> None:
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

    state.runner = RecordingCommandRunner(handler=handler)

    result = CliRunner().invoke(cli, ["new", "--user", "agent", "--yes"], obj=state)
    assert result.exit_code == 0, result.output

    config = load_config(state.config_path)
    assert len(config.agents) == 1
    assert config.agents[0].user_name == "agent"
    assert config.agents[0].su_as_agent_group == "su-as-agent"
    assert config.agents[0].entrypoint == str(state.home_root / "agent" / "su_as_agent")

    assert (state.home_root / "agent/README.md").exists()
    assert (
        state.home_root
        / "agent/.config/agent-as-another-unix-user/su_as_agent-src/main.c"
    ).exists()
    assert any(call.args[:1] == ("groupadd",) for call in state.runner.calls)
    assert any(call.args[:1] == ("useradd",) for call in state.runner.calls)
    assert any(call.args[:1] == ("make",) for call in state.runner.calls)


def test_run_releases_config_lock_before_continuing(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[[agents]]\n"
        'user_name = "agent"\n'
        'su_as_agent_group = "su-as-agent"\n'
        'entrypoint = "/tmp/su_as_agent"\n'
        "bootstrapped = true\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli, ["-C", str(config_path), "run", "--user", "agent", "echo"]
    )
    assert result.exit_code == 0, result.output
    assert result.output.index("Relese config lock") < result.output.index("Now exit")


# def test_run_forwards_command_to_entrypoint() -> None:
#     with tempfile.TemporaryDirectory() as tmp:
#         tmp_path = Path(tmp)
#         config_path = tmp_path / "config.toml"
#         home_root = tmp_path / "home"
#         entrypoint = tmp_path / "su_as_agent"
#         entrypoint.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
#         entrypoint.chmod(0o755)

#         config_path.write_text(
#             "[[agents]]\n"
#             f'user_name = "agent"\n'
#             f'su_as_agent_group = "su-as-agent"\n'
#             f"entrypoint = {str(entrypoint)!r}\n",
#             encoding="utf-8",
#         )

#         runner = RecordingCommandRunner()
#         state = AppState(
#             config_path=config_path, home_root=home_root, runner=runner, is_root=False
#         )

#         result = CliRunner().invoke(
#             cli, ["run", "--user", "agent", "--", "echo", "hello"], obj=state
#         )
#         assert result.exit_code == 0, result.output
#         assert runner.calls[-1].args == (str(entrypoint), "echo", "hello")


# def test_list_marks_missing_user_broken() -> None:
#     with tempfile.TemporaryDirectory() as tmp:
#         tmp_path = Path(tmp)
#         config_path = tmp_path / "config.toml"
#         home_root = tmp_path / "home"
#         save_config(
#             config_path,
#             Config(
#                 path=config_path,
#                 agents=[
#                     AgentConfig(
#                         user_name="ghost",
#                         su_as_agent_group="su-as-ghost",
#                         entrypoint=str(tmp_path / "ghost" / "su_as_agent"),
#                     )
#                 ],
#             ),
#         )

#         def handler(call):
#             if call.args[:2] == ("setfacl", "--version"):
#                 return CompletedProcess(
#                     args=list(call.args), returncode=0, stdout="setfacl 2.0", stderr=""
#                 )
#             if call.args[:2] == ("id", "-nG"):
#                 return CompletedProcess(
#                     args=list(call.args), returncode=0, stdout="su-as-ghost", stderr=""
#                 )
#             if call.args[:3] == ("getent", "passwd", "ghost"):
#                 return CompletedProcess(
#                     args=list(call.args), returncode=2, stdout="", stderr=""
#                 )
#             if call.args[:3] == ("getent", "group", "su-as-ghost"):
#                 return CompletedProcess(
#                     args=list(call.args),
#                     returncode=0,
#                     stdout="su-as-ghost:x:1000:\n",
#                     stderr="",
#                 )
#             return CompletedProcess(
#                 args=list(call.args), returncode=0, stdout="", stderr=""
#             )

#         runner = RecordingCommandRunner(handler=handler)
#         state = AppState(
#             config_path=config_path, home_root=home_root, runner=runner, is_root=False
#         )

#         result = CliRunner().invoke(cli, ["list"], obj=state)
#         assert result.exit_code == 0, result.output
#         assert "ghost" in result.output
#         assert "missing UNIX user" in result.output


# def test_config_open_creates_file_and_shows_spinner_when_locked(
#     monkeypatch, capsys
# ) -> None:
#     with tempfile.TemporaryDirectory() as tmp:
#         tmp_path = Path(tmp)
#         config_path = tmp_path / "config.toml"

#         state = {"calls": 0}

#         def fake_flock(_fd, op):
#             if op & config_module.fcntl.LOCK_UN:
#                 return None
#             state["calls"] += 1
#             if state["calls"] == 1 and op & config_module.fcntl.LOCK_NB:
#                 raise BlockingIOError()
#             return None

#         monkeypatch.setattr(config_module.fcntl, "flock", fake_flock)
#         monkeypatch.setattr(config_module.time, "sleep", lambda _seconds: None)

#         with config_module.Config.open(config_path) as config:
#             config.upsert_agent(
#                 config_module.AgentConfig(
#                     user_name="agent",
#                     su_as_agent_group="su-as-agent",
#                     entrypoint="/tmp/agent/su_as_agent",
#                 )
#             )

#         captured = capsys.readouterr()
#         assert config_path.exists()
#         assert "Waiting for lock on" in captured.err
