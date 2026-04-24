from __future__ import annotations

from pathlib import Path

import click
from click import echo, style

from ..config import MountConfig
from ..system import resolve_agent_home
from . import AppState, cli


@cli.group("mount")
def mount_group() -> None:
    """Manage external path accesses for an agent via bind mounts."""


@mount_group.command("add")
@click.option("--agent", "-a", "user_name", default="agent", show_default=True)
@click.option(
    "--rw",
    "read_write",
    is_flag=True,
    default=False,
    help="Mount read-write instead of read-only.",
)
@click.argument(
    "source", type=click.Path(exists=True, resolve_path=True, path_type=Path)
)
@click.argument("target", required=False, default=None, type=Path)
@click.pass_obj
def mount_add(
    state: AppState, user_name: str, read_write: bool, source: Path, target: Path | None
) -> None:
    """Give the agent access to an external path via a bind mount"""
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

    read_only = not read_write
    mount = MountConfig(source=str(source), target=str(target), read_only=read_only)
    agent.mounts.append(mount)
    state.config.upsert_agent(agent)
    state.config.save()

    mode = "read-only" if read_only else "read-write"
    echo(
        f"Added {style(user_name, fg='green')} {mode} access to "
        f"{style(source, fg='yellow')} (mounted at {style(target, fg='yellow')})"
    )


@mount_group.command("remove")
@click.option("--agent", "-a", "user_name", default="agent", show_default=True)
@click.argument("source_or_target")
@click.pass_obj
def mount_remove(state: AppState, user_name: str, source_or_target: str) -> None:
    """Remove an access bind mount for a given agent."""
    agent = state.config.get_agent(user_name)
    if agent is None:
        raise click.ClickException(f"unknown agent {user_name!r}")

    mount = next(
        (
            m
            for m in agent.mounts
            if m.source == source_or_target or m.target == source_or_target
        ),
        None,
    )
    if mount is None:
        raise click.ClickException(
            f"agent {style(user_name, fg='yellow')} has no recorded access bind mount with source or target {style(source_or_target, fg='yellow')}"
        )

    agent.mounts.remove(mount)
    state.config.upsert_agent(agent)
    state.config.save()

    echo(
        f"Removed {style(user_name, fg='green')} access bind mount {style(mount.source, fg='yellow')} -> {style(mount.target, fg='yellow')}"
    )


@mount_group.command("list")
@click.option("--agent", "-a", "user_name", default="agent", show_default=True)
@click.pass_obj
def mount_list(state: AppState, user_name: str) -> None:
    """List all existing access bind mounts for a given agent."""
    agent = state.config.get_agent(user_name)
    if agent is None:
        raise click.ClickException(f"unknown agent {user_name!r}")

    if not agent.mounts:
        echo(f"No access bind mount configured for agent {style(user_name, fg='green')}.")
    else:
        for mount in agent.mounts:
            mode = "ro" if mount.read_only else "rw"
            echo(f"{mount.source} -> {mount.target} [{mode}]")
