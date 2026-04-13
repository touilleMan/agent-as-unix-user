from __future__ import annotations

from pathlib import Path

from click import echo, style
import click

from ..system import resolve_agent_home
from . import AppState, cli


@cli.command("delete")
@click.option("--agent", "-a", "user_name", default="agent", show_default=True)
@click.option(
    "--delete-home", "-H", is_flag=True, help="Also delete the agent home dir"
)
@click.option("--yes", "-y", is_flag=True, help="Do not ask for confirmation.")
@click.pass_obj
def delete_agent(state: AppState, user_name: str, delete_home: bool, yes: bool) -> None:
    agent = state.config.get_agent(user_name)
    if agent is None:
        raise click.ClickException(f"unknown agent {user_name!r}")

    # Sanity check and detect what can be deleted

    delete_user = True
    agent_uid = None
    result = state.runner.run(
        ["id", "--user", agent.user_name],
        text=True,
        capture_output=True,
        quiet=True,
        check=False,
    )
    assert isinstance(result.stdout, str)
    if result.returncode != 0:
        echo(
            f"Skipping removal of user {style(agent.user_name, fg='red')}: user doesn't exist"
        )
        delete_user = False
    else:
        agent_uid = int(result.stdout)

    delete_group = True
    result = state.runner.run(
        ["id", "--group", "--name", agent.user_name],
        text=True,
        capture_output=True,
        quiet=True,
    )
    assert isinstance(result.stdout, str)
    if result.stdout.strip() != agent.su_as_agent_group:
        echo(
            f"Skipping removal of group {style(agent.su_as_agent_group, fg='red')}: "
            f"user {style(agent.user_name, fg='yellow')} is not part of it"
        )
        delete_group = False

    agent_home = resolve_agent_home(agent.user_name)

    agent_home_to_delete = None
    if delete_home:
        if agent_home is None:
            echo(
                f"Skipping removal of home: {style('~' + agent.user_name, fg='yellow')} doesn't exist"
            )
            delete_home = False
        else:
            try:
                stat = agent_home.stat()
            except FileNotFoundError:
                echo(
                    f"Skipping removal of home: {style(agent_home, fg='yellow')} doesn't exist"
                )
                delete_home = False
            else:
                if agent_uid is not None and stat.st_uid != agent_uid:
                    echo(
                        f"Skipping removal of home: {style(agent_home, fg='red')} "
                        f"is not owned by user {style(agent.user_name, fg='yellow')} "
                        f"(expected UID {style(agent_uid, fg='green')} got {style(stat.st_uid, fg='red')})"
                    )
                    delete_home = False
                else:
                    agent_home_to_delete = agent_home

    # Actual deletion

    agent.bootstrapped = False
    state.config.upsert_agent(agent)
    state.config.save()

    # Remove symlinks created for external accesses
    human_home = Path.home()
    if agent_home is not None:
        for access_path in agent.acl_external_accesses:
            try:
                relative = Path(access_path).relative_to(human_home)
                symlink_path = agent_home / relative
                state.runner.run(
                    ["sudo", "-u", user_name, "rm", "-f", str(symlink_path)],
                    check=False,
                )
            except ValueError:
                pass

    if delete_user:
        state.runner.run(["sudo", "userdel", agent.user_name], check=False)
    if delete_group:
        state.runner.run(["sudo", "groupdel", agent.su_as_agent_group], check=False)

    if agent_home_to_delete:
        state.runner.run(["sudo", "rm", "-rf", str(agent_home_to_delete)], check=False)

    # Remove ACL traverse permission on the human's home
    state.runner.run(
        ["sudo", "setfacl", "--remove", f"user:{user_name}", str(human_home)],
        check=False,
    )

    state.config.remove_agent(user_name)
    state.config.save()

    echo(f"Deleted agent {style(user_name, fg='green')}")
