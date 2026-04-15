from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

import click

from ..version import __version__
from ..config import default_config_path, Config
from ..runner import CommandRunner, SubprocessRunner


@dataclass(slots=True)
class AppState:
    config_path: Path
    config: Config
    home_root: Path
    runner: CommandRunner
    is_root: bool


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--config",
    "config_path",
    "-C",
    type=click.Path(path_type=Path),
    default=default_config_path(),
    help="Path to the configuration file.",
)
@click.version_option(__version__)
@click.pass_context
def cli(ctx: click.Context, config_path: Path) -> None:
    # Check if we are in the tests
    if ctx.obj is not None:
        return

    ctx.obj = AppState(
        config_path=config_path,
        # Lock the configuration for the duration of the command
        # to prevent concurrent operation (except for `au run`)
        config=ctx.with_resource(Config.open(config_path)),
        home_root=Path("/home"),
        runner=SubprocessRunner(),
        is_root=(os.geteuid() == 0),
    )

    # TODO: Sanity check to ensure the home directory doesn't give access to
    #       other users (e.g. mode 750 or 700).


# Import does a side effect that register the sub command in `cli`
from . import access as _access  # noqa: E402,F401
from . import delete as _delete  # noqa: E402,F401
from . import info as _info  # noqa: E402,F401
from . import list as _list  # noqa: E402,F401
from . import new as _new  # noqa: E402,F401
from . import run as _run  # noqa: E402,F401


def main() -> None:
    cli()


__all__ = ("AppState", "cli", "main")
