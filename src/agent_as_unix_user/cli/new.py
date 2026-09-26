from __future__ import annotations

import click
from click import style
import getpass
from pathlib import Path
import pwd
import shutil
import shlex
import tempfile

from ..config import AgentConfig
from ..system import (
    acl_supported,
    ENTRYPOINT_SRC_MAIN_C,
    entrypoint_src_makefile,
    agent_readme_content,
    entrypoint_install_dir,
    entrypoint_install_path,
    expected_su_as_agent_group,
    expected_home,
    compute_sha256_fingerprint,
)
from . import AppState, cli


@cli.command("new")
@click.option("--agent", "-a", "user_name", default="agent", show_default=True)
@click.option("--yes", "-y", is_flag=True, help="Do not ask for confirmation.")
@click.pass_obj
def new_agent(state: AppState, user_name: str, yes: bool) -> None:
    """Create a new agent with its UNIX user, group, and entrypoint."""
    config_path = state.config_path
    su_as_agent_group = expected_su_as_agent_group(user_name)
    home = expected_home(user_name, state.home_root)
    entrypoint_dir = entrypoint_install_dir(user_name)
    entrypoint = entrypoint_install_path(user_name)
    # The uid allowed to run the entrypoint is the human creating the agent.
    # Derive it from the same account we add to the agent's group below.
    caller_uid = str(pwd.getpwnam(getpass.getuser()).pw_uid)

    if state.config.get_agent(user_name) is not None:
        raise click.ClickException(
            f"Agent {style(user_name, fg='red')} already exists in {style(config_path, fg='yellow')}"
        )

    if not yes and not click.confirm(
        f"Create agent {style(user_name, fg='green')} in {home} and configure group {su_as_agent_group!r}?",
        default=False,
    ):
        raise click.Abort()

    if not acl_supported(state.runner):
        raise click.ClickException(
            "ACL support is required but not available on this system"
        )

    # Update config to keep track of the fact a new agent is
    # being created (important for cleanup if we crash...)

    agent_config = AgentConfig(
        user_name=user_name,
        su_as_agent_group=su_as_agent_group,
        entrypoint=str(entrypoint),
        entrypoint_sha256="<unknown>",
        bootstrapped=False,
        mounts=[],
    )
    state.config.upsert_agent(agent_config)
    state.config.save()

    # Create the UNIX stuff

    # Create the su_as_agent UNIX group
    state.runner.run(["sudo", "groupadd", su_as_agent_group])
    # Create UNIX user
    bash_path = shutil.which("bash")
    if bash_path:
        shell_opts = ["--shell", bash_path]
    else:
        # Use default shell
        shell_opts = []
    state.runner.run(
        [
            "sudo",
            "useradd",
            *shell_opts,
            "--no-user-group",
            "--create-home",
            "--home-dir",
            str(home),
            "--gid",
            su_as_agent_group,
            user_name,
        ]
    )
    # Give access to the su_as_agent UNIX group to our user
    state.runner.run(
        [
            "sudo",
            "usermod",
            "--append",
            "--groups",
            su_as_agent_group,
            getpass.getuser(),
        ]
    )
    # Configure setgid for the agent's home dir with the su_as_agent UNIX group
    # This way all file/directory created within the home dir will have the
    # su_as_agent UNIX group instead of the default group of the creator.
    # This is useful to ensure all files created in the home can be modified
    # by all members of the su_as_agent UNIX group.
    state.runner.run(["sudo", "chmod", "2770", str(home)])
    # But that's not all! We also need to ensure the umask a user is using won't
    # create a file that cannot be modified by the group.
    # For this we use the ACL defaults to ensure the group always has RWX rights.
    state.runner.run(
        [
            "sudo",
            "setfacl",
            "--modify",
            f"default:group:{su_as_agent_group}:rwx",
            str(home),
        ]
    )

    # Since the UNIX group has just been created, our current session doesn't
    # have access to it!
    # So any write operation must be done as a shell command with `sg <UNIX group>`
    # as prefix (which execute command as this group ID).

    # Convoluted wait to copy a file since we must use `sg`
    def _sg_copy_file(target: Path, input: str) -> None:
        state.runner.run(
            ["sg", su_as_agent_group, "-c", f"tee {target}"],
            input=input,
            # tee writes on stdout so silence this
            capture_output=True,
        )

    _sg_copy_file(
        home / "README.md",
        agent_readme_content(
            agent_config,
            config_path,
            home,
        ),
    )

    # Build in a throw-away dir owned by the human (never the agent-controlled
    # home), then install root-owned: the agent must not be able to modify the
    # binary it invokes with root privileges.

    target_uid = state.runner.run(
        ["id", "--user", user_name],
        capture_output=True,
        text=True,
        check=False,
        quiet=True,
    ).stdout.strip()
    int(target_uid)  # Sanity check to ensure we got the user ID
    target_gid = state.runner.run(
        ["id", "--group", user_name],
        capture_output=True,
        text=True,
        check=False,
        quiet=True,
    ).stdout.strip()
    int(target_gid)  # Sanity check to ensure we got the user ID

    build_dir = Path(tempfile.mkdtemp(prefix="au-entrypoint-"))
    try:
        (build_dir / "main.c").write_text(ENTRYPOINT_SRC_MAIN_C)
        (build_dir / "Makefile").write_text(
            entrypoint_src_makefile(
                target_uid=target_uid,
                target_gid=target_gid,
                caller_uid=caller_uid,
            )
        )
        state.runner.run(["make", "-C", str(build_dir)])

        # Root-owned install directory, not writable by the agent or its group.
        state.runner.run(["sudo", "mkdir", "-p", str(entrypoint_dir)])
        state.runner.run(["sudo", "chown", "root:root", str(entrypoint_dir)])
        state.runner.run(["sudo", "chmod", "755", str(entrypoint_dir)])
        # owner root + setuid (4750) so the binary runs as root then drops to the
        # agent; group su-as-agent so only group members may execute it.
        state.runner.run(
            [
                "sudo",
                "install",
                "-o",
                "root",
                "-g",
                su_as_agent_group,
                "-m",
                "4750",
                str(build_dir / "su_as_agent"),
                str(entrypoint),
            ]
        )
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)

    # Finally update again the config to acknowledge the agent is ready

    result = state.runner.run(
        [
            # The agent user has just been created, we should re-login to have
            # our groups being updated (so that `agent.su_as_agent_group` appears).
            # So we use sg here to instead force execute the command as group
            # `agent.su_as_agent_group` which works without even with re-login.
            "sg",
            "-",
            su_as_agent_group,
            "-c",
            shlex.join(["cat", str(entrypoint)]),
        ],
        capture_output=True,
        text=False,
        quiet=True,
    )
    assert isinstance(result.stdout, bytes)
    agent_config.entrypoint_sha256 = compute_sha256_fingerprint(result.stdout)

    agent_config.bootstrapped = True

    state.config.upsert_agent(agent_config)
    state.config.save()

    click.echo(f"Created agent {style(user_name, fg='green')}")
