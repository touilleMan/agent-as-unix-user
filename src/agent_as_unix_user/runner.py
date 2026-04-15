from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol
from click import echo, style
import subprocess


class CommandRunner(Protocol):
    def run(
        self,
        args: list[str] | tuple[str, ...],
        *,
        cwd: str | Path | None = None,
        check: bool = True,
        capture_output: bool = False,
        text: bool = True,
        input: str | None = None,
        quiet: bool = False,
        **kwargs,
    ) -> subprocess.CompletedProcess: ...


class SubprocessRunner:
    def run(
        self,
        args: list[str] | tuple[str, ...],
        *,
        cwd: str | Path | None = None,
        check: bool = True,
        capture_output: bool = False,
        text: bool = True,
        input: str | None = None,
        quiet: bool = False,
        **kwargs,
    ) -> subprocess.CompletedProcess:
        if not quiet:
            display_args = [
                f"'{a.replace("'", "\\'")}'" if " " in a else a for a in args
            ]
            if cwd:
                display_cmd = (
                    style(f"cd {cwd}", fg="grey") + " && " + " ".join(display_args)
                )
            else:
                display_cmd = " ".join(display_args)
            echo(style("$ ", fg="yellow") + display_cmd)
        return subprocess.run(  # noqa: S603
            list(args),
            cwd=str(cwd) if cwd is not None else None,
            check=check,
            capture_output=capture_output,
            text=text,
            input=input,
            **kwargs,
        )


@dataclass(frozen=True, slots=True)
class RecordingCommandCall:
    args: tuple[str, ...]
    cwd: Path | None
    check: bool
    capture_output: bool
    text: bool
    input: str | None
    kwargs: dict[str, Any]


class RecordingCommandRunner:
    """A test-friendly runner that records all calls.

    By default, every command succeeds. Tests can inspect ``calls`` or
    register a custom ``handler`` to simulate outputs and failures.
    """

    def __init__(
        self,
        handler: Callable[[RecordingCommandCall], subprocess.CompletedProcess]
        | None = None,
    ) -> None:
        self.calls: list[RecordingCommandCall] = []
        self.handler = handler

    def run(
        self,
        args: list[str] | tuple[str, ...],
        *,
        cwd: str | Path | None = None,
        check: bool = True,
        capture_output: bool = False,
        text: bool = True,
        input: str | None = None,
        quiet: bool = False,
        **kwargs,
    ) -> subprocess.CompletedProcess:
        call = RecordingCommandCall(
            args=tuple(args),
            cwd=Path(cwd) if cwd is not None else None,
            check=check,
            capture_output=capture_output,
            text=text,
            input=input,
            kwargs=kwargs,
        )
        self.calls.append(call)

        if self.handler is not None:
            result = self.handler(call)
        else:
            result = subprocess.CompletedProcess(
                args=list(call.args),
                returncode=0,
                stdout="" if capture_output else None,
                stderr="" if capture_output else None,
            )

        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode,
                list(call.args),
                output=result.stdout,
                stderr=result.stderr,
            )
        return result
