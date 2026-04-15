from __future__ import annotations

import click
from click import echo, style

from ..system import healthcheck_agent
from . import AppState, cli


@cli.command("info")
@click.option("--agent", "-a", "user_name", default="agent", show_default=True)
@click.pass_obj
def info(state: AppState, user_name: str) -> None:
    agent = state.config.get_agent(user_name)
    if agent is None:
        raise click.ClickException(f"unknown agent {user_name!r}")

    health = healthcheck_agent(state.runner, agent)
    if health.is_ok:
        echo(f"{style(agent.user_name, fg='green')}")
    else:
        echo(f"{style(agent.user_name, fg='green')} ({style('BROKEN', fg='red')})")
    echo(f"  home: {style(health.home, fg='yellow')}")
    echo(f"  group: {style(agent.su_as_agent_group, fg='yellow')}")
    echo(f"  entrypoint: {style(agent.entrypoint, fg='yellow')}")
    if agent.mounts:
        echo("  mounts:")
        for mount in agent.mounts:
            echo(
                f"    - {style(mount.source, fg='yellow')} -> {style(mount.target, fg='yellow')}"
            )
    else:
        echo("  mounts: none")
    if health.errors:
        echo(style("  issues:", fg="red"))
        for reason in health.errors:
            echo(f"    - {reason}")
