from __future__ import annotations

from pathlib import Path

import click
from click import echo, style

from . import AppState, cli


@cli.group("access")
def access_group() -> None:
    """Manage external path accesses for an agent via ACL."""


@access_group.command("add")
@click.option("--agent", "-a", "user_name", default="agent", show_default=True)
@click.argument("path", type=click.Path(exists=True, resolve_path=True, path_type=Path))
@click.pass_obj
def access_add(state: AppState, user_name: str, path: Path) -> None:
    """Give the agent read-only access to PATH via ACL."""
    agent = state.config.get_agent(user_name)
    if agent is None:
        raise click.ClickException(f"unknown agent {user_name!r}")

    resolved = str(path)
    if resolved in agent.acl_external_accesses:
        raise click.ClickException(
            f"agent {style(user_name, fg='yellow')} already has access to {style(resolved, fg='yellow')}"
        )

    # Ensure the human user's home directory has at least o+x (751) so the
    # agent can resolve paths through it without being able to list contents.
    human_home = Path.home()
    state.runner.run(["sudo", "chmod", "o+x", str(human_home)])

    # Set ACL: read + execute for the agent user on the target path and
    # a default ACL so newly created files/dirs inherit the same access.
    state.runner.run(
        ["sudo", "setfacl", "--recursive", "--modify", f"user:{user_name}:rX", resolved]
    )
    state.runner.run(
        [
            "sudo",
            "setfacl",
            "--recursive",
            "--modify",
            f"default:user:{user_name}:rX",
            resolved,
        ]
    )

    agent.acl_external_accesses.append(resolved)
    state.config.upsert_agent(agent)
    state.config.save()

    echo(
        f"Granted {style(user_name, fg='green')} read-only access to {style(resolved, fg='yellow')}"
    )


@access_group.command("remove")
@click.option("--agent", "-a", "user_name", default="agent", show_default=True)
@click.argument("path", type=click.Path(resolve_path=True, path_type=Path))
@click.pass_obj
def access_remove(state: AppState, user_name: str, path: Path) -> None:
    """Revoke the agent's ACL access to PATH."""
    agent = state.config.get_agent(user_name)
    if agent is None:
        raise click.ClickException(f"unknown agent {user_name!r}")

    resolved = str(path)
    if resolved not in agent.acl_external_accesses:
        raise click.ClickException(
            f"agent {style(user_name, fg='yellow')} has no recorded access to {style(resolved, fg='yellow')}"
        )

    # Remove ACL entries for the agent user
    state.runner.run(
        ["sudo", "setfacl", "--recursive", "--remove", f"user:{user_name}", resolved]
    )
    state.runner.run(
        [
            "sudo",
            "setfacl",
            "--recursive",
            "--remove",
            f"default:user:{user_name}",
            resolved,
        ]
    )

    agent.acl_external_accesses.remove(resolved)
    state.config.upsert_agent(agent)
    state.config.save()

    echo(
        f"Revoked {style(user_name, fg='green')} access to {style(resolved, fg='yellow')}"
    )
