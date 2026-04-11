from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

import click

from .config import (
    default_config_path,
    get_agent,
    load_config,
    remove_agent,
    upsert_agent,
)
from .models import AgentConfig
from .runner import CommandRunner, SubprocessRunner
from .system import (
    acl_supported,
    agent_main_c,
    agent_makefile,
    agent_readme_content,
    agent_source_dir,
    current_user_name,
    expected_group_name,
    expected_home,
    healthcheck_agent,
    require_root,
    resolve_agent_home,
)

VERSION = "0.1.0"


@dataclass(slots=True)
class AppState:
    config_path: Path
    home_root: Path
    runner: CommandRunner
    is_root: bool


def make_default_state(config_path: Path | None = None) -> AppState:
    return AppState(
        config_path=config_path or default_config_path(),
        home_root=Path("/home"),
        runner=SubprocessRunner(),
        is_root=(os.geteuid() == 0),
    )


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--config",
    "config_path",
    "-C",
    type=click.Path(path_type=Path),
    default=None,
    help="Path to the configuration file.",
)
@click.version_option(VERSION)
@click.pass_context
def cli(ctx: click.Context, config_path: Path | None) -> None:
    if ctx.obj is None:
        ctx.obj = make_default_state(config_path)
    elif config_path is not None:
        ctx.obj.config_path = config_path


@cli.command("new")
@click.option("--user", "user_name", default="agent", show_default=True)
@click.option("--yes", is_flag=True, help="Do not ask for confirmation.")
@click.pass_obj
def new_agent(state: AppState, user_name: str, yes: bool) -> None:
    require_root(state.is_root)

    config_path = state.config_path
    group_name = expected_group_name(user_name)
    home = expected_home(user_name, state.home_root)
    entrypoint = home / "su_as_agent"
    source_dir = agent_source_dir(home)

    if get_agent(config_path, user_name) is not None:
        raise click.ClickException(
            f"agent {user_name!r} already exists in {config_path}"
        )

    if not yes and not click.confirm(
        f"Create agent {user_name!r} in {home} and configure group {group_name!r}?",
        default=False,
    ):
        raise click.Abort()

    if not acl_supported(state.runner):
        raise click.ClickException(
            "ACL support is required but not available on this system"
        )

    uid_result = state.runner.run(
        ["id", "-u", user_name], capture_output=True, text=True, check=False
    )
    target_uid = (uid_result.stdout or "").strip() or "1000"

    commands = [
        ["groupadd", group_name],
        [
            "useradd",
            "--create-home",
            "--home-dir",
            str(home),
            "--gid",
            group_name,
            user_name,
        ],
        ["usermod", "-a", "-G", group_name, current_user_name()],
        ["chmod", "2775", str(home)],
        ["setfacl", "-m", f"g:{group_name}:rwx", str(home)],
        ["setfacl", "-m", f"d:g:{group_name}:rwx", str(home)],
    ]
    for command in commands:
        state.runner.run(command)

    source_dir.mkdir(parents=True, exist_ok=True)
    source_dir.joinpath("main.c").write_text(agent_main_c(target_uid), encoding="utf-8")
    source_dir.joinpath("Makefile").write_text(
        agent_makefile(target_uid), encoding="utf-8"
    )
    source_dir.joinpath("README.md").write_text(
        "Source directory for the su_as_agent entrypoint.\n", encoding="utf-8"
    )

    state.runner.run(["make", "-C", str(source_dir)])
    state.runner.run(["cp", str(source_dir / "su_as_agent"), str(entrypoint)])
    state.runner.run(["chown", f"{user_name}:{group_name}", str(entrypoint)])
    state.runner.run(["chmod", "4750", str(entrypoint)])

    home.mkdir(parents=True, exist_ok=True)
    home.joinpath("README.md").write_text(
        agent_readme_content(
            AgentConfig(
                user_name=user_name,
                su_as_agent_group=group_name,
                entrypoint=str(entrypoint),
            ),
            config_path,
            home,
        ),
        encoding="utf-8",
    )

    upsert_agent(
        config_path,
        AgentConfig(
            user_name=user_name,
            su_as_agent_group=group_name,
            entrypoint=str(entrypoint),
        ),
    )
    click.echo(f"Created agent {user_name!r}")


@cli.command("delete")
@click.option("--user", "user_name", default="agent", show_default=True)
@click.option("--dry-run", is_flag=True, help="Print actions without executing them.")
@click.option("--yes", is_flag=True, help="Do not ask for confirmation.")
@click.pass_obj
def delete_agent(state: AppState, user_name: str, dry_run: bool, yes: bool) -> None:
    require_root(state.is_root)

    config_path = state.config_path
    agent = get_agent(config_path, user_name)
    group_name = agent.su_as_agent_group if agent else expected_group_name(user_name)
    home = (
        expected_home(user_name, state.home_root)
        if agent is None
        else resolve_agent_home(state.runner, user_name)
    )
    entrypoint = Path(agent.entrypoint) if agent else home / "su_as_agent"
    source_dir = agent_source_dir(home)

    if not yes and not click.confirm(
        f"Delete agent {user_name!r} and remove all data from {home}?",
        default=False,
    ):
        raise click.Abort()

    actions = [
        ["rm", "-rf", str(entrypoint)],
        ["rm", "-rf", str(source_dir)],
        ["rm", "-rf", str(home)],
        ["userdel", user_name],
        ["groupdel", group_name],
    ]

    if dry_run:
        for action in actions:
            click.echo("DRY-RUN: " + " ".join(action))
    else:
        for action in actions:
            result = state.runner.run(
                action, check=False, capture_output=True, text=True
            )
            if result.returncode != 0:
                click.echo(
                    f"warning: command failed but continuing: {' '.join(action)}",
                    err=True,
                )

    remove_agent(config_path, user_name)
    click.echo(f"Deleted agent {user_name!r}")


@cli.command("list")
@click.pass_obj
def list_agents(state: AppState) -> None:
    agents = load_config(state.config_path)
    if not agents:
        click.echo("No agents configured.")
        return

    for agent in agents:
        health = healthcheck_agent(state.runner, agent)
        click.echo(f"{agent.user_name}: {health.status.upper()}")
        click.echo(f"  home: {health.home}")
        click.echo(f"  group: {agent.su_as_agent_group}")
        click.echo(f"  entrypoint: {agent.entrypoint}")
        if health.reasons:
            click.echo("  issues:")
            for reason in health.reasons:
                click.echo(f"    - {reason}")


@cli.command("run")
@click.option("--user", "user_name", default="agent", show_default=True)
@click.argument("command", nargs=-1, required=True)
@click.pass_obj
def run_as_agent(state: AppState, user_name: str, command: tuple[str, ...]) -> None:
    if state.is_root:
        raise click.ClickException("au run must not be executed as root")

    agent = get_agent(state.config_path, user_name)
    if agent is None:
        raise click.ClickException(f"unknown agent {user_name!r}")

    entrypoint = Path(agent.entrypoint)
    if not entrypoint.exists():
        raise click.ClickException(f"entrypoint does not exist: {entrypoint}")
    if not os.access(entrypoint, os.X_OK):
        raise click.ClickException(f"entrypoint is not executable: {entrypoint}")

    result = state.runner.run(
        [str(entrypoint), *command], check=False, capture_output=False, text=True
    )
    if result.returncode != 0:
        raise click.ClickException(
            f"agent command failed with exit code {result.returncode}"
        )


def main() -> None:
    cli()
