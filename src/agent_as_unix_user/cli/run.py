from __future__ import annotations

import shlex
import click
import sys
from click import style
from pathlib import Path
import os

from . import AppState, cli
from ..system import compute_sha256_fingerprint


KEPT_ENVIRON_VARIALBES = ("LANG", "TERM")


def validate_environs(
    ctx: click.Context, param: click.Parameter, value: tuple[str, ...]
) -> dict[str, str]:
    cooked = {}
    for item in value:
        try:
            k, v = item.split("=")
        except ValueError:
            raise click.BadParameter(
                f"Invalid value `{item}`, environment must be passed in `KEY=VALUE` format"
            )
        cooked[k] = v
    return cooked


@cli.command("run")
@click.option("--agent", "-a", "user_name", default="agent", show_default=True)
@click.option(
    "--env",
    "-e",
    "environs",
    metavar="KEY=VALUE",
    multiple=True,
    help="Environ variable to pass on",
    callback=validate_environs,
)
@click.option(
    "--directory",
    "-d",
    "working_directory",
    type=click.Path(path_type=Path, resolve_path=True),
    default=None,
    help="""
        Working directory to use when running the command as the agent.
        If not specified, will auto-detect if current directory is a bind mount source and use the corresponding target, or default to the agent's $HOME.
    """,
)
@click.argument("command", nargs=-1, required=True)
@click.pass_obj
@click.pass_context
def run_as_agent(
    ctx: click.Context,
    state: AppState,
    user_name: str,
    environs: dict[str, str],
    working_directory: Path | None,
    command: tuple[str, ...],
) -> None:
    """Run a command as the agent UNIX user."""
    agent = state.config.get_agent(user_name)
    if agent is None:
        raise click.ClickException(f"unknown agent {user_name!r}")

    # Auto-detect if current directory (or one of its parents) is a bind mount source,
    # so we can start the agent shell in the corresponding bind mount target.
    if working_directory is None:
        current_dir = Path.cwd()
        # Find the most specific mount (longest path that is a parent of current_dir)
        best_match = None
        best_match_length = -1
        for mount in agent.mounts:
            try:
                # Check if current directory is the source or a subdirectory of it
                relative_path = current_dir.relative_to(mount.source)
                # Use the mount with the longest source path (most specific)
                if len(mount.source) > best_match_length:
                    best_match = mount
                    best_match_length = len(mount.source)
            except ValueError:
                # Current directory is not under this mount source, try next
                continue

        if best_match is not None:
            # Current directory is under a mount source, use the corresponding target
            relative_path = current_dir.relative_to(best_match.source)
            working_directory = Path(best_match.target) / relative_path
        else:
            # No matching mount found, use current directory (will be relative to agent home)
            working_directory = current_dir

    # Drop the advisory lock on the configuration file since the subcommand is going
    # to take an arbitrary long time.
    #
    # The config resource is attached to the root click context in `cli()`,
    # so closing the current command context would not release it yet.
    ctx.find_root().close()

    # Sanity check

    result = state.runner.run(
        [
            # If the agent user has just been created, we should re-login
            # to have our groups being updated (so that `agent.su_as_agent_group`
            # appears).
            # So we use sg here to instead force execute the command as group
            # `agent.su_as_agent_group` which works without even with re-login.
            "sg",
            "-",
            agent.su_as_agent_group,
            "-c",
            shlex.join(["cat", agent.entrypoint]),
        ],
        capture_output=True,
        text=False,
        quiet=True,
    )
    assert isinstance(result.stdout, bytes)
    entrypoint_sha256 = compute_sha256_fingerprint(result.stdout)
    if entrypoint_sha256 != agent.entrypoint_sha256:
        raise click.ClickException(
            f"entrypoint {style(agent.entrypoint, fg='yellow')} has been tempered: "
            f"expected hash {style(agent.entrypoint_sha256, fg='green')}, "
            f"got {style(entrypoint_sha256, fg='red')}"
        )

    mount_args: list[str] = []
    for m in agent.mounts:
        flag = "--mount-ro" if m.read_only else "--mount-rw"
        mount_args.extend([flag, m.source, m.target])

    # We use bash as a login, interactive shell to ensure the agent's environment
    # (PATH, etc.) is properly set up by sourcing .profile, .bashrc, etc.
    # Then pass the actual command to run with the -c flag

    # Quote the working directory and command for safe shell execution
    command_to_run = " ".join(shlex.quote(c) for c in command)
    if working_directory:
        command_to_run = f"cd {shlex.quote(str(working_directory))} && { command_to_run }"
    full_command = [
            "bash",
            # The --login flag makes bash source /etc/profile and ~/.profile (or ~/.bash_profile, ~/.bash_login)
            "--login",
            # The -i flag makes bash interactive, which prevents .bashrc from returning early due to
            # the common "case $- in *i*) ;; *) return;; esac" guard.
            # Note we only enable interactive if our own shell is itself interactive.
            "-i" if sys.stdin.isatty() else "",
            "-c",
            command_to_run
        ]

    result = state.runner.run(
        [
            # If the agent user has just been created, we should re-login
            # to have our groups being updated (so that `agent.su_as_agent_group`
            # appears).
            # So we use sg here to instead force execute the command as group
            # `agent.su_as_agent_group` which works without even with re-login.
            "sg",
            "-",
            agent.su_as_agent_group,
            "-c",
            shlex.join([agent.entrypoint, *mount_args, "--", *full_command]),
        ],
        check=False,
        capture_output=False,
        text=True,
        env={
            **{
                k: v
                for k in KEPT_ENVIRON_VARIALBES
                if (v := os.environ.get(k)) is not None
            },
            **environs,
        },
    )
    raise SystemExit(result.returncode)
