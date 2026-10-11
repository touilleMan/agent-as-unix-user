from __future__ import annotations

import os
import shlex
import shutil
import socket
from pathlib import Path

import click
from click import echo, style

from ..system import (
    add_known_hosts_entry,
    agent_sshd_dir,
    remove_known_hosts_entries,
    resolve_agent_home,
    verify_entrypoint,
    vscode_ssh_remote_authority,
)
from . import AppState, cli


def _free_port() -> int:
    # Tiny race between grabbing the port here and sshd binding it,
    # acceptable given sshd will loudly fail in this (unlikely) case.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _log_tail(log: Path) -> str:
    """Last lines of the sshd log, for error reporting."""
    try:
        content = log.read_text(errors="replace")
    except OSError:
        return f"(cannot read {log})"
    return "\n".join(content.splitlines()[-8:]) or f"(empty log: {log})"


@cli.command("sshd")
@click.option("--agent", "-a", "user_name", default="agent", show_default=True)
@click.option("--port", "-p", "port", type=int)
@click.pass_obj
@click.pass_context
def sshd(ctx: click.Context, state: AppState, user_name: str, port: int) -> None:
    """Run an sshd as the agent user."""
    agent = state.config.get_agent(user_name)
    if agent is None:
        raise click.ClickException(f"unknown agent {user_name!r}")
    if not agent.ssh_identity:
        raise click.ClickException(
            f"agent {style(user_name, fg='green')} has no ssh identity configured "
            "(re-create it with `au delete --and-recreate`)"
        )

    a_home = resolve_agent_home(agent.user_name)
    if a_home is None:
        raise click.ClickException(f"~{user_name} doesn't exist")

    a_sshd_dir = agent_sshd_dir(a_home)
    missing = [
        label
        for label, path in [
            ("sshd config", a_sshd_dir / "sshd_config"),
            ("sshd host key", a_sshd_dir / "host_ed25519_key"),
            ("sshd host key public part", a_sshd_dir / "host_ed25519_key.pub"),
            ("sshd authorized keys", a_sshd_dir / "authorized_keys"),
            ("ssh identity", Path(agent.ssh_identity)),
        ]
        if not path.exists()
    ]
    if missing:
        raise click.ClickException(
            f"missing sshd prerequisites: {style(', '.join(missing), fg='yellow')} "
            "(run `au info` for details)"
        )

    verify_entrypoint(state.runner, agent)

    host = "127.0.0.1"
    port = port or _free_port()

    # Pre-seed the human's known_hosts with the sshd host key for this port, so
    # the ssh clients (including VS Code's Remote-SSH) connect without any
    # fingerprint confirmation. The entry is removed when this command stops.
    known_hosts = Path.home() / ".ssh" / "known_hosts"
    host_pub = (a_sshd_dir / "host_ed25519_key.pub").read_text().strip()
    add_known_hosts_entry(known_hosts, f"[{host}]:{port} {host_pub}")

    # Leave a marker so `au vscode-url` can find the running sshd
    runtime_dir = state.config_path / "sshd-runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    runtime_marker = runtime_dir / str(port)
    runtime_marker.touch()

    # VS Code's Remote-SSH builds its own ssh command, which only uses the
    # ssh-agent (i.e. it cannot be told about our dedicated identity), so try to
    # load the identity in the agent. The plain ssh command printed below
    # doesn't need this since it passes the identity with `-i`.
    ssh_add_ok = False
    if os.environ.get("SSH_AUTH_SOCK"):
        ssh_add_ok = (
            state.runner.run(
                ["ssh-add", agent.ssh_identity],
                check=False,
                capture_output=True,
                text=True,
                quiet=True,
            ).returncode
            == 0
        )

    # Drop the advisory lock on the configuration file since the subcommand is going
    # to take an arbitrary long time.
    #
    # The config resource is attached to the root click context in `cli()`,
    # so closing the current command context would not release it yet.
    ctx.find_root().close()

    authority = vscode_ssh_remote_authority(agent.user_name, port, host)
    echo(f"sshd listening on {style(f'{host}:{port}', fg='green')} (Ctrl+C to stop)")
    echo(
        f"  connect: ssh -i {style(agent.ssh_identity, fg='yellow')} "
        f"-p {port} {agent.user_name}@{host}"
    )
    echo(
        f"  vscode: code --folder-uri "
        f"{shlex.quote(f'vscode-remote://{authority}{a_home}')}"
    )
    if not ssh_add_ok:
        echo(
            f"  note: identity not available to your ssh-agent, VS Code won't be able to "
            f"authenticate until you run: ssh-add {agent.ssh_identity}"
        )

    sshd_path = shutil.which("sshd") or "/usr/sbin/sshd"
    sshd_log = a_sshd_dir / "sshd.log"
    try:
        result = state.runner.run(
            [
                "sg",
                "-",
                agent.su_as_agent_group,
                "-c",
                shlex.join(
                    [
                        agent.entrypoint,
                        "--",
                        sshd_path,
                        # -D keeps sshd in the foreground, so the command runs
                        # until the user hits Ctrl+C (sshd dies with us, being
                        # in the same process group).
                        "-D",
                        "-E",
                        str(sshd_log),
                        "-f",
                        str(a_sshd_dir / "sshd_config"),
                        # The port is not part of the sshd config since it is
                        # random (so that concurrent runs don't collide).
                        "-p",
                        str(port),
                    ]
                ),
            ],
            # The setuid entrypoint requires a scrubbed environment
            env={},
            check=False,
            capture_output=False,
            text=True,
        )
        if result.returncode != 0:
            raise click.ClickException(
                f"sshd exited with code {style(str(result.returncode), fg='red')}, "
                f"tail of {style(str(sshd_log), fg='yellow')}:\n{_log_tail(sshd_log)}"
            )
    except KeyboardInterrupt:
        pass
    finally:
        runtime_marker.unlink(missing_ok=True)
        remove_known_hosts_entries(known_hosts, port, host)
