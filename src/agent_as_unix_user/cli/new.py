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
    agent_config_dir,
    entrypoint_src_makefile,
    agent_readme_content,
    agent_entrypoint_src_dir,
    expected_su_as_agent_group,
    expected_agent_home,
    compute_sha256_fingerprint,
    sshd_config_content,
    agent_sshd_dir,
)
from . import AppState, cli
from .mount import _apply_source_acl


@cli.command("new")
@click.option("--agent", "-a", "user_name", default="agent", show_default=True)
@click.option("--yes", "-y", is_flag=True, help="Do not ask for confirmation.")
@click.pass_obj
def new_agent(state: AppState, user_name: str, yes: bool) -> None:
    """Create a new agent with its UNIX user, group, and entrypoint."""
    config_path = state.config_path
    su_as_agent_group = expected_su_as_agent_group(user_name)
    home = expected_agent_home(user_name, state.home_root)
    entrypoint = agent_config_dir(home) / "su_as_agent"

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
        bootstrapped=False,
        user_name=user_name,
        su_as_agent_group=su_as_agent_group,
        entrypoint=str(entrypoint),
        entrypoint_sha256="<unknown>",
        ssh_identity=None,
        mounts=[],
    )

    create_agent(state, agent_config)

    click.echo(f"Created agent {style(user_name, fg='green')}")


def create_agent(state: AppState, agent_config: AgentConfig) -> None:
    """
    This command:
    - Saves the agent in the config file
    - Creates the UNIX user/group/home
    - Copies & compiles the entrypoint
    - Creates the sshd config
    - Configure the mount pathes ACLs
    """

    assert agent_config.bootstrapped is False
    state.config.upsert_agent(agent_config)
    state.config.save()

    a_user_name = agent_config.user_name
    su_as_agent_group = agent_config.su_as_agent_group
    h_config_path = state.config_path
    h_ssh_config_dir = h_config_path / "ssh"
    a_home = expected_agent_home(a_user_name, state.home_root)
    a_config_dir = agent_config_dir(a_home)
    a_entrypoint = a_config_dir / "su_as_agent"
    a_entrypoint_src = agent_entrypoint_src_dir(a_home)

    # Convoluted way to copy a file into the agent's home: we must use `sg`
    # (to execute as the su-as-agent group) since our current session may not
    # have the freshly-created group in it yet.
    def _sg_copy_file(target: Path, input: str) -> None:
        state.runner.run(
            ["sg", su_as_agent_group, "-c", f"tee {target}"],
            input=input,
            # tee writes on stdout so silence this
            capture_output=True,
        )

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
            str(a_home),
            "--gid",
            su_as_agent_group,
            a_user_name,
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
    state.runner.run(["sudo", "chmod", "2770", str(a_home)])
    # But that's not all! We also need to ensure the umask a user is using won't
    # create a file that cannot be modified by the group.
    # For this we use the ACL defaults to ensure the group always has RWX rights.
    state.runner.run(
        [
            "sudo",
            "setfacl",
            "--modify",
            f"default:group:{su_as_agent_group}:rwx",
            str(a_home),
        ]
    )

    # Overwrite the config if this home is being reused (e.g. `au delete --and-recreate`)

    state.runner.run(["rm", "-rf", str(a_config_dir)])

    # Since the UNIX group has just been created, our current session doesn't
    # have access to it!
    # So any write operation must be done as a shell command with `sg <UNIX group>`
    # as prefix (which execute command as this group ID).

    _sg_copy_file(
        a_home / "README.md",
        agent_readme_content(
            agent_config,
            h_config_path,
            a_home,
        ),
    )

    # Compile and install the entrypoint

    target_uid = state.runner.run(
        ["id", "--user", a_user_name],
        capture_output=True,
        text=True,
        check=False,
        quiet=True,
    ).stdout.strip()
    int(target_uid)  # Sanity check to ensure we got the user ID
    target_gid = state.runner.run(
        ["id", "--group", a_user_name],
        capture_output=True,
        text=True,
        check=False,
        quiet=True,
    ).stdout.strip()
    int(target_gid)  # Sanity check to ensure we got the user ID

    state.runner.run(["sg", su_as_agent_group, "-c", f"mkdir -p {a_entrypoint_src}"])

    _sg_copy_file(a_entrypoint_src / "main.c", ENTRYPOINT_SRC_MAIN_C)
    _sg_copy_file(
        a_entrypoint_src / "Makefile",
        entrypoint_src_makefile(target_uid=target_uid, target_gid=target_gid),
    )

    state.runner.run(["sg", su_as_agent_group, "-c", f"make -C {a_entrypoint_src}"])
    state.runner.run(
        [
            "sg",
            su_as_agent_group,
            "-c",
            f"mv --force {a_entrypoint_src / 'su_as_agent'} {a_entrypoint}",
        ]
    )
    # Here is the secret sauce:
    # - Set root as the entrypoint binary's owner.
    # - Set the SetUID bit on the entrypoint binary. This gives it the
    #   file owner's privileges (i.e. root) instead of the caller's.
    # - The group is set to the su-as-agent group so only members can execute it.
    # This allows the binary to use root privileges to drop the caller's
    # groups and become permanently the agent user.
    state.runner.run(["sudo", "chown", f"root:{su_as_agent_group}", str(a_entrypoint)])
    # Note the leading `4` in chmod, this is the setuid bit
    state.runner.run(["sudo", "chmod", "4750", str(a_entrypoint)])

    # Configure the sshd prerequisites, used by `au sshd` to have the agent user
    # run a ssh server listening on localhost:
    # - a dedicated ssh identity whose public key is the only one accepted
    # - the sshd config/host key/authorized keys

    h_ssh_config_dir.mkdir(parents=True, exist_ok=True)
    h_ssh_identity = h_ssh_config_dir / f"{agent_config.user_name}_id_ed25519"
    h_ssh_identity_pub = h_ssh_config_dir / f"{h_ssh_identity}.pub"
    state.runner.run(["rm", "-f", str(h_ssh_identity), str(h_ssh_identity_pub)])
    state.runner.run(
        [
            "ssh-keygen",
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-C",
            f"au-{a_user_name}",
            "-f",
            str(h_ssh_identity),
        ]
    )
    agent_config.ssh_identity = str(h_ssh_identity)

    a_sshd_dir = agent_sshd_dir(a_home)
    state.runner.run(
        ["sg", su_as_agent_group, "-c", f"mkdir -p {a_sshd_dir}"]
    )
    a_host_key = a_sshd_dir / "host_ed25519_key"
    state.runner.run(
        [
            "sg",
            su_as_agent_group,
            "-c",
            shlex.join(
                [
                    "ssh-keygen",
                    "-q",
                    "-t",
                    "ed25519",
                    "-N",
                    "",
                    "-C",
                    f"au-sshd-{a_user_name}",
                    "-f",
                    str(a_host_key),
                ]
            ),
        ]
    )
    state.runner.run(["chmod", "600", str(a_host_key)])
    state.runner.run(["sudo", "chown", a_user_name, str(a_host_key)])
    _sg_copy_file(a_sshd_dir / "sshd_config", sshd_config_content(a_home))
    _sg_copy_file(a_sshd_dir / "authorized_keys", h_ssh_identity_pub.read_text().strip() + "\n")

    # Re-apply the ACLs of the read-write bind mounts: `au delete` clears them,
    # so this is a no-op for a fresh `au new` (which has no mounts yet) and
    # restores the sources' accesses when recreating an agent with
    # `au delete --and-recreate`.
    for mount in agent_config.mounts:
        if mount.read_only:
            continue
        source = Path(mount.source)
        if source.exists():
            _apply_source_acl(state, source, su_as_agent_group)
        else:
            click.echo(
                f"Skipping rw access restoration on {style(mount.source, fg='yellow')}: source doesn't exist"
            )

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
            shlex.join(["cat", str(a_entrypoint)]),
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
