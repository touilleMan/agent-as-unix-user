from __future__ import annotations


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
    agent = state.config.get_agent(user_name)
    if agent is None:
        raise click.ClickException(f"unknown agent {user_name!r}")

    # Drop the advisory lock on the configuration file since the subcommand is going
    # to take an arbitrary long time.
    #
    # The config resource is attached to the root click context in `cli()`,
    # so closing the current command context would not release it yet.
    ctx.find_root().close()

    # entrypoint = Path(agent.entrypoint)
    # if not entrypoint.exists():
    #     raise click.ClickException(f"entrypoint does not exist: {entrypoint}")
    # if not os.access(entrypoint, os.X_OK):
    #     raise click.ClickException(f"entrypoint is not executable: {entrypoint}")

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
            f"{agent.entrypoint} {' '.join(command)}",
        ],
        check=False,
        capture_output=False,
        text=True,
    )
    if result.returncode != 0:
        raise click.ClickException(
            f"agent command failed with exit code {result.returncode}"
        )
