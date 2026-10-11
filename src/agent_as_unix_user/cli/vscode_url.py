from __future__ import annotations

import socket
from pathlib import Path
from urllib.parse import quote

import click
from click import echo

from ..system import vscode_ssh_remote_authority
from . import AppState, cli


def _port_is_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def _running_sshd_ports(state: AppState) -> list[int]:
    """Ports of the sshd instances started by `au sshd`.

    Stale markers (e.g. left by a crashed `au sshd`, or written by a
    still-starting one) are ignored, but not removed: `au sshd` writes its
    marker before spawning the sshd, so a non-listening port may just mean
    the sshd is about to bind it.
    """
    ports = []
    runtime_dir = state.config_path / "sshd-runtime"
    for marker in runtime_dir.glob("*"):
        if marker.name.isdigit() and _port_is_listening(int(marker.name)):
            ports.append(int(marker.name))
    return sorted(ports)


@cli.command("vscode-url")
@click.option("--agent", "-a", "user_name", default="agent", show_default=True)
@click.option(
    "--port",
    type=int,
    default=None,
    help="sshd port, needed when several `au sshd` are running.",
)
@click.option(
    "--open",
    "do_open",
    is_flag=True,
    help="Open the link with xdg-open instead of printing it.",
)
@click.argument("path", type=click.Path(path_type=Path, resolve_path=True))
@click.pass_obj
def vscode_url(
    state: AppState, user_name: str, port: int | None, do_open: bool, path: Path
) -> None:
    """Print a link opening PATH in VS Code connected to the agent's sshd."""
    agent = state.config.get_agent(user_name)
    if agent is None:
        raise click.ClickException(f"unknown agent {user_name!r}")

    # Translate bind mount sources into their agent-side target, so a path
    # coming from the human's side points at the same file in the remote.
    for mount in sorted(agent.mounts, key=lambda m: len(m.source), reverse=True):
        try:
            path = Path(mount.target) / path.relative_to(mount.source)
            break
        except ValueError:
            continue

    if port is None:
        ports = _running_sshd_ports(state)
        if not ports:
            raise click.ClickException("no running `au sshd` (start one first)")
        if len(ports) > 1:
            raise click.ClickException(
                f"several `au sshd` running on ports {ports}, specify one with `--port`"
            )
        port = ports[0]

    authority = vscode_ssh_remote_authority(agent.user_name, port)
    # VS Code's link handler (see `getWindowOpenableFromProtocolUrl` in VS Code)
    # opens the path as a folder, unless it ends with `:<line>`, in which case
    # it opens the file at that line.
    quoted_path = quote(str(path))
    suffix = ":1" if path.is_file() else ""
    link = f"vscode://vscode-remote/{authority}{quoted_path}{suffix}"

    if do_open:
        state.runner.run(["xdg-open", link], check=False)
    else:
        echo(link)
