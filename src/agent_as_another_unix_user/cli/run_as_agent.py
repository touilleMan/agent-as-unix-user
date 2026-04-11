from __future__ import annotations

from pathlib import Path
import os

import click

from ..config import get_agent
from . import AppState, cli


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
