from __future__ import annotations

import shlex
import click
from click import style
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
@click.argument("command", nargs=-1, required=True)
@click.pass_obj
@click.pass_context
def run_as_agent(
    ctx: click.Context,
    state: AppState,
    user_name: str,
    environs: dict[str, str],
    command: tuple[str, ...],
) -> None:
    agent = state.config.get_agent(user_name)
    if agent is None:
        raise click.ClickException(f"unknown agent {user_name!r}")

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
            shlex.join([agent.entrypoint, *command]),
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
