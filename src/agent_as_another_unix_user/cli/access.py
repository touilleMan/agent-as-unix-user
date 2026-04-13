from __future__ import annotations

import stat
from pathlib import Path

import click
from click import echo, style

from ..system import resolve_agent_home
from . import AppState, cli


@cli.group("access")
def access_group() -> None:
    """Manage external path accesses for an agent via symlinks."""


def _check_path_traversable(target: Path) -> list[str]:
    """
    Check that each component of target can be traversed by anybody.

    It is expected that the human uses umak 022 and hence all its
    file&folder are readable by anybody.
    The real security is that the human is expected to not give any
    right on his home directory to others, hence preventing anyone
    from accessing his home... unless an ACL right has been given,
    which is what we do to have a symlink that works.
    """
    warnings: list[str] = []
    for parent in target.parents:
        try:
            st = parent.stat()
        except OSError:
            warnings.append(f"{parent} — cannot stat")
            continue
        # Check if 'other' has execute permission
        if not (st.st_mode & stat.S_IXOTH):
            warnings.append(
                f"{parent} — not traversable by other users (mode {stat.filemode(st.st_mode)})"
            )

    try:
        is_file = target.is_file()
        st = target.stat()
    except OSError:
        warnings.append(f"{target} — cannot stat")
    else:
        if not is_file and not (st.st_mode & stat.S_IXOTH):
            warnings.append(
                f"{target} — not traversable by other users (mode {stat.filemode(st.st_mode)})"
            )

    return warnings


@access_group.command("add")
@click.option("--agent", "-a", "user_name", default="agent", show_default=True)
@click.argument("path", type=click.Path(exists=True, resolve_path=True, path_type=Path))
@click.pass_obj
def access_add(state: AppState, user_name: str, path: Path) -> None:
    """Give the agent access to PATH via a symlink in the agent's home."""
    agent = state.config.get_agent(user_name)
    if agent is None:
        raise click.ClickException(f"unknown agent {user_name!r}")

    resolved = str(path)

    # Sanity check: target must be inside the human's home directory
    human_home = Path.home()
    try:
        path.relative_to(human_home)
    except ValueError:
        raise click.ClickException(
            f"target {style(resolved, fg='yellow')} is not inside "
            f"your home directory {style(str(human_home), fg='yellow')}"
        )
    path_relative_to_human_home = path.relative_to(human_home)

    if resolved in agent.acl_external_accesses:
        raise click.ClickException(
            f"agent {style(user_name, fg='yellow')} already has access to {style(resolved, fg='yellow')}"
        )

    # Check traversability of each path component and warn
    warnings = _check_path_traversable(path)
    for warning in warnings:
        echo(style("warning: ", fg="yellow") + warning)

    # Record the access in the config first (crash-safe bookkeeping)
    agent.acl_external_accesses.append(resolved)
    state.config.upsert_agent(agent)
    state.config.save()

    # Create a symlink in the agent's home that mirrors the relative path
    agent_home = resolve_agent_home(agent.user_name)
    if not agent_home:
        raise click.ClickException(
            f"{style('~' + agent.user_name, fg='yellow')} doesn't exist"
        )
    symlink_path = agent_home / path_relative_to_human_home

    # Create parent directories as needed (as the agent group)
    state.runner.run(["mkdir", "-p", str(symlink_path.parent)])
    state.runner.run(
        ["ln", "--symbolic", "--force", "--no-dereference", resolved, str(symlink_path)]
    )

    echo(
        f"Granted {style(user_name, fg='green')} access to "
        f"{style(resolved, fg='yellow')} via {style(str(symlink_path), fg='yellow')}"
    )


@access_group.command("remove")
@click.option("--agent", "-a", "user_name", default="agent", show_default=True)
@click.argument("path", type=click.Path(resolve_path=True, path_type=Path))
@click.pass_obj
def access_remove(state: AppState, user_name: str, path: Path) -> None:
    """Revoke the agent's access to PATH by removing the symlink."""
    agent = state.config.get_agent(user_name)
    if agent is None:
        raise click.ClickException(f"unknown agent {user_name!r}")

    resolved = str(path)
    if resolved not in agent.acl_external_accesses:
        raise click.ClickException(
            f"agent {style(user_name, fg='yellow')} has no recorded access to {style(resolved, fg='yellow')}"
        )

    # Remove the symlink from the agent's home
    human_home = Path.home()
    agent_home = resolve_agent_home(agent.user_name)
    if agent_home is not None:
        try:
            relative = path.relative_to(human_home)
            symlink_path = agent_home / relative
            state.runner.run(
                ["rm", "-f", str(symlink_path)],
                check=False,
            )
        except ValueError:
            pass

    agent.acl_external_accesses.remove(resolved)
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

    if not agent.acl_external_accesses:
        echo(f"No access path configured for agent {style(user_name, fg='green')}.")
    else:
        for access in agent.acl_external_accesses:
            echo(access)
