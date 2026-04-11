from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

import click

from ..version import __version__
from ..config import default_config_path
from ..runner import CommandRunner, SubprocessRunner


@dataclass(slots=True)
class AppState:
    config_path: Path
    home_root: Path
    runner: CommandRunner
    is_root: bool


def make_default_state(config_path: Path | None = None) -> AppState:
    return AppState(
        config_path=config_path or default_config_path(),
        home_root=Path("/home"),
        runner=SubprocessRunner(),
        is_root=(os.geteuid() == 0),
    )


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--config",
    "config_path",
    "-C",
    type=click.Path(path_type=Path),
    default=None,
    help="Path to the configuration file.",
)
@click.version_option(__version__)
@click.pass_context
def cli(ctx: click.Context, config_path: Path | None) -> None:
    if ctx.obj is None:
        ctx.obj = make_default_state(config_path)
    elif config_path is not None:
        ctx.obj.config_path = config_path


from . import delete_agent as _delete_agent  # noqa: E402,F401
from . import list_agents as _list_agents  # noqa: E402,F401
from . import new_agent as _new_agent  # noqa: E402,F401
from . import run_as_agent as _run_as_agent  # noqa: E402,F401


def main() -> None:
    cli()


__all__ = ("AppState", "cli", "main", "make_default_state")
