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
@click.pass_obj
def access_add(state: AppState, user_name: str, source: Path) -> None:
    """Give the agent read-only access to PATH via a bind mount."""
    agent = state.config.get_agent(user_name)
    if agent is None:
        raise click.ClickException(f"unknown agent {user_name!r}")

    # Sanity check: target must be inside the human's home directory
    human_home = Path.home()
    try:
        source.relative_to(human_home)
    except ValueError:
        raise click.ClickException(
            f"target {style(source, fg='yellow')} is not inside "
            f"your home directory {style(str(human_home), fg='yellow')}"
        )
    path_relative_to_human_home = source.relative_to(human_home)

    # Compute the target path in the agent's home
    agent_home = resolve_agent_home(agent.user_name)
    if not agent_home:
        raise click.ClickException(
            f"{style('~' + agent.user_name, fg='yellow')} doesn't exist"
        )
    target = str(agent_home / path_relative_to_human_home)

    mount = MountConfig(source=str(source), target=target)
    agent.mounts.append(mount)
    state.config.upsert_agent(agent)
    state.config.save()

    echo(
        f"Granted {style(user_name, fg='green')} read-only access to "
        f"{style(source, fg='yellow')} (mounted at {style(target, fg='yellow')})"
    )


@access_group.command("remove")
@click.option("--agent", "-a", "user_name", default="agent", show_default=True)
@click.argument("path", type=click.Path(resolve_path=True, path_type=Path))
@click.pass_obj
def access_remove(state: AppState, user_name: str, path: Path) -> None:
    """Revoke the agent's access to PATH."""
    agent = state.config.get_agent(user_name)
    if agent is None:
        raise click.ClickException(f"unknown agent {user_name!r}")

    resolved = str(path)
    mount = next((m for m in agent.mounts if m.source == resolved), None)
    if mount is None:
        raise click.ClickException(
            f"agent {style(user_name, fg='yellow')} has no recorded access to {style(resolved, fg='yellow')}"
        )

    agent.mounts.remove(mount)
    state.config.upsert_agent(agent)
    state.config.save()

    echo(
        f"Revoked {style(user_name, fg='green')} access to {style(resolved, fg='yellow')}"
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
