from __future__ import annotations

import click

from ..config import load_config
from ..system import healthcheck_agent
from . import AppState, cli


@cli.command("list")
@click.pass_obj
def list_agents(state: AppState) -> None:
    config = load_config(state.config_path)
    if not config:
        click.echo("No agents configured.")
        return

    for agent in config.agents:
        health = healthcheck_agent(state.runner, agent)
        click.echo(f"{agent.user_name}: {health.status.upper()}")
        click.echo(f"  home: {health.home}")
        click.echo(f"  group: {agent.su_as_agent_group}")
        click.echo(f"  entrypoint: {agent.entrypoint}")
        if health.reasons:
            click.echo("  issues:")
            for reason in health.reasons:
                click.echo(f"    - {reason}")
