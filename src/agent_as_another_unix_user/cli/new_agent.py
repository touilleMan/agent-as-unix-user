from __future__ import annotations

import click

from ..config import get_agent, upsert_agent, AgentConfig
from ..system import (
    acl_supported,
    agent_main_c,
    agent_makefile,
    agent_readme_content,
    agent_source_dir,
    current_user_name,
    expected_group_name,
    expected_home,
    require_root,
)
from . import AppState, cli


@cli.command("new")
@click.option("--user", "user_name", default="agent", show_default=True)
@click.option("--yes", is_flag=True, help="Do not ask for confirmation.")
@click.pass_obj
def new_agent(state: AppState, user_name: str, yes: bool) -> None:
    require_root(state.is_root)

    config_path = state.config_path
    group_name = expected_group_name(user_name)
    home = expected_home(user_name, state.home_root)
    entrypoint = home / "su_as_agent"
    source_dir = agent_source_dir(home)

    if get_agent(config_path, user_name) is not None:
        raise click.ClickException(
            f"agent {user_name!r} already exists in {config_path}"
        )

    if not yes and not click.confirm(
        f"Create agent {user_name!r} in {home} and configure group {group_name!r}?",
        default=False,
    ):
        raise click.Abort()

    if not acl_supported(state.runner):
        raise click.ClickException(
            "ACL support is required but not available on this system"
        )

    uid_result = state.runner.run(
        ["id", "-u", user_name], capture_output=True, text=True, check=False
    )
    target_uid = (uid_result.stdout or "").strip() or "1000"

    commands = [
        ["groupadd", group_name],
        [
            "useradd",
            "--create-home",
            "--home-dir",
            str(home),
            "--gid",
            group_name,
            user_name,
        ],
        ["usermod", "-a", "-G", group_name, current_user_name()],
        ["chmod", "2775", str(home)],
        ["setfacl", "-m", f"g:{group_name}:rwx", str(home)],
        ["setfacl", "-m", f"d:g:{group_name}:rwx", str(home)],
    ]
    for command in commands:
        state.runner.run(command)

    source_dir.mkdir(parents=True, exist_ok=True)
    source_dir.joinpath("main.c").write_text(agent_main_c(target_uid), encoding="utf-8")
    source_dir.joinpath("Makefile").write_text(
        agent_makefile(target_uid), encoding="utf-8"
    )
    source_dir.joinpath("README.md").write_text(
        "Source directory for the su_as_agent entrypoint.\n", encoding="utf-8"
    )

    state.runner.run(["make", "-C", str(source_dir)])
    state.runner.run(["cp", str(source_dir / "su_as_agent"), str(entrypoint)])
    state.runner.run(["chown", f"{user_name}:{group_name}", str(entrypoint)])
    state.runner.run(["chmod", "4750", str(entrypoint)])

    agent_config = AgentConfig(
        user_name=user_name,
        su_as_agent_group=group_name,
        entrypoint=str(entrypoint),
    )


    home.mkdir(parents=True, exist_ok=True)
    home.joinpath("README.md").write_text(
        agent_readme_content(
            agent_config,
            config_path,
            home,
        ),
        encoding="utf-8",
    )

    upsert_agent(
        config_path,
        agent_config,
    )
    click.echo(f"Created agent {user_name!r}")
