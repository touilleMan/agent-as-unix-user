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

    # TODO: Sanity check to ensure the home directory uses xx0 access rights.
    #       This is important to ensure the agent user cannot read its content
    #       and try to leak secrets.
    #       If this is detected, a warning should be displayed.
    #       A parameter `sanity_check_home_rights` can be set to `False`
    #       (it defaults to `True`) to disable this sanity check.


from . import delete_agent as _delete_agent  # noqa: E402,F401
from . import list_agents as _list_agents  # noqa: E402,F401
from . import new_agent as _new_agent  # noqa: E402,F401
from . import run_as_agent as _run_as_agent  # noqa: E402,F401


def main() -> None:
    cli()


__all__ = ("AppState", "cli", "main")
