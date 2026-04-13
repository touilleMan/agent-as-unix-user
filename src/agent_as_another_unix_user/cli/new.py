from __future__ import annotations

import click
from click import style
import getpass
from pathlib import Path
import shutil
import shlex

from ..config import AgentConfig
from ..system import (
    acl_supported,
    ENTRYPOINT_SRC_MAIN_C,
    entrypoint_src_makefile,
    agent_readme_content,
    entrypoint_src_dir,
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
    config_path = state.config_path
    su_as_agent_group = expected_su_as_agent_group(user_name)
    home = expected_home(user_name, state.home_root)
    entrypoint = home / "su_as_agent"
    entrypoint_src = entrypoint_src_dir(home)

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
        acl_external_accesses=[],
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

    # Grant the agent user execute (traverse) permission on the human's home
    # directory via ACL. This allows the agent to resolve symlinks pointing
    # into the human's home without being able to list its contents.
    human_home = Path.home()
    state.runner.run(
        ["sudo", "setfacl", "--modify", f"user:{user_name}:--x", str(human_home)]
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

    # Compile and install the entrypoint

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

    state.runner.run(["sg", su_as_agent_group, "-c", f"mkdir -p {entrypoint_src}"])

    _sg_copy_file(entrypoint_src / "main.c", ENTRYPOINT_SRC_MAIN_C)
    _sg_copy_file(
        entrypoint_src / "Makefile",
        entrypoint_src_makefile(target_uid=target_uid, target_gid=target_gid),
    )

    state.runner.run(["sg", su_as_agent_group, "-c", f"make -C {entrypoint_src}"])
    state.runner.run(
        [
            "sg",
            su_as_agent_group,
            "-c",
            f"mv --force {entrypoint_src / 'su_as_agent'} {entrypoint}",
        ]
    )
    # Here is the secret sauce:
    # - Set root as the entrypoint binary's owner.
    # - Set the SetUID bit on the entrypoint binary. This gives it the
    #   file owner's privileges (i.e. root) instead of the caller's.
    # - The group is set to the su-as-agent group so only members can execute it.
    # This allows the binary to use root privileges to drop the caller's
    # groups and become permanently the agent user.
    state.runner.run(["sudo", "chown", f"root:{su_as_agent_group}", str(entrypoint)])
    # Note the leading `4` in chmod, this is the setuid bit
    state.runner.run(["sudo", "chmod", "4750", str(entrypoint)])

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
