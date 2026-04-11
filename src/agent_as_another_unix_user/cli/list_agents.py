from __future__ import annotations

import click
from click import echo, style

from ..system import healthcheck_agent
from . import AppState, cli


@cli.command("list")
@click.pass_obj
def list_agents(state: AppState) -> None:
    if not state.config.agents:
        echo("No agents configured.")
        return

    for agent in state.config.agents:
        health = healthcheck_agent(state.runner, agent)
        if health.is_ok:
            echo(f"{style(agent.user_name, fg='green')}")
        else:
            echo(f"{style(agent.user_name, fg='red')}")
        echo(f"  home: {style(health.home, fg='yellow')}")
        echo(f"  group: {style(agent.su_as_agent_group, fg='yellow')}")
        echo(f"  entrypoint: {style(agent.entrypoint, fg='yellow')}")
        if health.errors:
            echo(style("  issues:", fg="red"))
            for reason in health.errors:
                echo(f"    - {reason}")
