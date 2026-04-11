from __future__ import annotations

from pathlib import Path
import os

import click

from . import AppState, cli


@cli.command("run")
@click.option("--user", "user_name", default="agent", show_default=True)
@click.argument("command", nargs=-1, required=True)
@click.pass_obj
@click.pass_context
def run_as_agent(
    ctx: click.Context, state: AppState, user_name: str, command: tuple[str, ...]
) -> None:
    import time

    time.sleep(100)
    agent = state.config.get_agent(user_name)
    if agent is None:
        raise click.ClickException(f"unknown agent {user_name!r}")

    entrypoint = Path(agent.entrypoint)
    if not entrypoint.exists():
        raise click.ClickException(f"entrypoint does not exist: {entrypoint}")
    if not os.access(entrypoint, os.X_OK):
        raise click.ClickException(f"entrypoint is not executable: {entrypoint}")

    # Drop the advisory lock on the configuration file since the subcommand is going
    # to take an arbitrary long time
    ctx.close()

    result = state.runner.run(
        [str(entrypoint), *command], check=False, capture_output=False, text=True
    )
    if result.returncode != 0:
        raise click.ClickException(
            f"agent command failed with exit code {result.returncode}"
        )
