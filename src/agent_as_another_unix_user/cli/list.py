from __future__ import annotations

import click
from click import echo, style

from . import AppState, cli


@cli.command("list")
@click.pass_obj
def list(state: AppState) -> None:
    if not state.config.agents:
        echo("No agents configured.")
        return

    for agent in state.config.agents:
        if agent.user_name == "agent":
            echo(f"{agent.user_name} ({style('default', fg='green')})")
        else:
            echo(f"{agent.user_name}")
