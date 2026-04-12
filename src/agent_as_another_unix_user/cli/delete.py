from __future__ import annotations

from pathlib import Path

import click

from ..config import Config
from ..system import (
    expected_su_as_agent_group,
    expected_home,
    resolve_agent_home,
    entrypoint_src_dir,
)
from . import AppState, cli


@cli.command("delete")
@click.option("--agent", "-a", "user_name", default="agent", show_default=True)
@click.option(
    "--dry-run", "-d", is_flag=True, help="Print actions without executing them."
)
@click.option("--yes", "-y", is_flag=True, help="Do not ask for confirmation.")
@click.pass_obj
def delete_agent(state: AppState, user_name: str, dry_run: bool, yes: bool) -> None:
    config_path = state.config_path
    agent = state.config.get_agent(user_name)
    group_name = (
        agent.su_as_agent_group if agent else expected_su_as_agent_group(user_name)
    )
    home = (
        expected_home(user_name, state.home_root)
        if agent is None
        else resolve_agent_home(state.runner, user_name)
    )
    entrypoint = Path(agent.entrypoint) if agent else home / "su_as_agent"
    source_dir = entrypoint_src_dir(home)

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

    with Config.open(config_path) as config:
        config.remove_agent(user_name)
    click.echo(f"Deleted agent {user_name!r}")
