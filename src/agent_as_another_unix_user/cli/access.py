from __future__ import annotations

from pathlib import Path

import click
from click import echo, style

from ..config import MountConfig
from ..system import resolve_agent_home
from . import AppState, cli


@cli.group("access")
def access_group() -> None:
    """Manage external path accesses for an agent via bind mounts."""


@access_group.command("add")
@click.option("--agent", "-a", "user_name", default="agent", show_default=True)
@click.argument(
    "source", type=click.Path(exists=True, resolve_path=True, path_type=Path)
)
@click.argument("target", required=False, default=None, type=Path)
@click.pass_obj
def access_add(state: AppState, user_name: str, source: Path, target: Path | None) -> None:
    """Give the agent read-only access to PATH via a bind mount."""
    agent = state.config.get_agent(user_name)
    if agent is None:
        raise click.ClickException(f"unknown agent {user_name!r}")

    human_home = Path.home()
    agent_home = resolve_agent_home(agent.user_name)
    if not agent_home:
        raise click.ClickException(
            f"{style('~' + agent.user_name, fg='yellow')} doesn't exist"
        )

    # Security check: source must be inside the human's home directory
    try:
        source_relative_to_human_home = source.relative_to(human_home)
    except ValueError:
        raise click.ClickException(
            f"source {style(source, fg='yellow')} is not inside "
            f"your home directory {style(str(human_home), fg='yellow')}"
        )

    if target is None:
        # Compute the target path in the agent's home
        target = agent_home / source_relative_to_human_home

    # Security check: target must be inside the agent's home directory
    try:
        target.relative_to(agent_home)
    except ValueError:
        raise click.ClickException(
            f"target {style(target, fg='yellow')} is not inside "
            f"the agent directory {style(str(agent_home), fg='yellow')}"
        )

    mount = MountConfig(source=str(source), target=str(target))
    agent.mounts.append(mount)
    state.config.upsert_agent(agent)
    state.config.save()

    echo(
        f"Granted {style(user_name, fg='green')} read-only access to "
        f"{style(source, fg='yellow')} (mounted at {style(target, fg='yellow')})"
    )


@access_group.command("remove")
@click.option("--agent", "-a", "user_name", default="agent", show_default=True)
@click.argument("target", type=Path)
@click.pass_obj
def access_remove(state: AppState, user_name: str, target: Path) -> None:
    """Revoke the agent's access to PATH."""
    agent = state.config.get_agent(user_name)
    if agent is None:
        raise click.ClickException(f"unknown agent {user_name!r}")

    mount = next((m for m in agent.mounts if m.target == str(target)), None)
    if mount is None:
        raise click.ClickException(
            f"agent {style(user_name, fg='yellow')} has no recorded access with target {style(target, fg='yellow')}"
        )

    agent.mounts.remove(mount)
    state.config.upsert_agent(agent)
    state.config.save()

    echo(
        f"Revoked {style(user_name, fg='green')} access {style(mount.source, fg='yellow')} -> {style(mount.target, fg='yellow')}"
    )


@access_group.command("list")
@click.option("--agent", "-a", "user_name", default="agent", show_default=True)
@click.pass_obj
def access_list(state: AppState, user_name: str) -> None:
    """List all existing access for a given agent."""
    agent = state.config.get_agent(user_name)
    if agent is None:
        raise click.ClickException(f"unknown agent {user_name!r}")

    if not agent.mounts:
        echo(f"No access path configured for agent {style(user_name, fg='green')}.")
    else:
        for mount in agent.mounts:
            echo(f"{mount.source} -> {mount.target}")
