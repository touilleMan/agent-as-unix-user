from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
import fcntl
import json
import os
import time
import tomllib
from typing import Iterator, TextIO
from click import style

from .utils import Spinner

DEFAULT_CONFIG_FILENAME = "agent-as-another-unix-user.toml"
DEFAULT_GROUP_PREFIX = "su-as-"


def default_config_path() -> Path:
    return Path.home() / ".config" / DEFAULT_CONFIG_FILENAME


@dataclass(slots=True)
class MountConfig:
    source: str
    "Absolute path on the human's side (e.g. /home/alice/foo/bar)"
    target: str
    "Absolute path on the agent's side (e.g. /home/agent/foo/bar)"
    read_only: bool = True
    "If True the mount is read-only; if False the agent can write to it."


@dataclass(slots=True)
class AgentConfig:
    user_name: str
    "UNIX user name"
    su_as_agent_group: str
    "UNIX group to be able to use the entrypoint and read/write the agent home"
    entrypoint: str
    "Path to the binary to execute to run command as the agent user"
    entrypoint_sha256: str
    """
    Keep a fingerprint of the entrypoint to detect any modification.

    This is important since the entrypoint is responsible for dropping
    the user's rights, so a malicious agent might want to modify it (which
    is possible in the first place since the entrypoint *must* be owned by
    the agent) in order to trick the human into doing something.
    """
    bootstrapped: bool
    """
    If the agent has been fully configured (UNIX user, group etc.).

    Might be `False` if new agent command couldn't finish...
    """
    mounts: list[MountConfig]
    "Bind mounts to set up when running as the agent"


@dataclass(slots=True)
class Config:
    path: Path
    agents: list[AgentConfig] = field(default_factory=list)
    disable_home_access_check: bool = False
    _fh: TextIO | None = field(default=None, repr=False, compare=False)
    _dirty: bool = field(default=False, repr=False, compare=False)

    def get_agent(self, user_name: str) -> AgentConfig | None:
        return next(
            (agent for agent in self.agents if agent.user_name == user_name), None
        )

    def upsert_agent(self, agent: AgentConfig) -> None:
        for idx, existing in enumerate(self.agents):
            if existing.user_name == agent.user_name:
                self.agents[idx] = agent
                self._dirty = True
                return
        self.agents.append(agent)
        self._dirty = True

    def remove_agent(self, user_name: str) -> None:
        filtered = [agent for agent in self.agents if agent.user_name != user_name]
        if len(filtered) != len(self.agents):
            self.agents = filtered
            self._dirty = True

    def to_toml(self) -> str:
        lines: list[str] = []
        if self.disable_home_access_check:
            lines.extend(
                [
                    f"disable_home_access_check = {json.dumps(self.disable_home_access_check)}",
                    "",
                ]
            )
        for agent in self.agents:
            lines.extend(
                [
                    "[[agents]]",
                    f"user_name = {json.dumps(agent.user_name)}",
                    f"su_as_agent_group = {json.dumps(agent.su_as_agent_group)}",
                    f"entrypoint = {json.dumps(agent.entrypoint)}",
                    f"entrypoint_sha256 = {json.dumps(agent.entrypoint_sha256)}",
                    f"bootstrapped = {json.dumps(agent.bootstrapped)}",
                    *(
                        f"[[agents.mounts]]\nsource = {json.dumps(m.source)}\ntarget = {json.dumps(m.target)}\nread_only = {json.dumps(m.read_only)}"
                        for m in agent.mounts
                    ),
                    "",
                ]
            )
        return "\n".join(lines).rstrip() + ("\n" if lines else "")

    def save(self) -> None:
        # TODO: In theory we should first write the new config in
        #       a temporary file then do a move for atomiicty, but
        #       we also need to take care of the lock...
        assert self._fh is not None, (
            "Config.save() can only be used inside Config.open()"
        )
        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write(self.to_toml())
        self._fh.flush()
        os.fsync(self._fh.fileno())
        self._dirty = False

    @classmethod
    @contextmanager
    def open(cls, path: Path) -> Iterator[Config]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)

        with path.open("r+", encoding="utf-8") as fh:
            spinner = None
            while True:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if not spinner:
                        spinner = Spinner(
                            f"Waiting for lock on {style(path, fg='yellow')}"
                        )
                    spinner.tick()
                    time.sleep(0.1)
            if spinner:
                spinner.stop()

            fh.seek(0)
            raw = fh.read().strip()
            data = tomllib.loads(raw)
            if raw:
                agents = [
                    AgentConfig(
                        user_name=str(item["user_name"]),
                        su_as_agent_group=str(item["su_as_agent_group"]),
                        entrypoint=str(item["entrypoint"]),
                        entrypoint_sha256=str(item["entrypoint_sha256"]),
                        bootstrapped=bool(item["bootstrapped"]),
                        mounts=[
                            MountConfig(
                                source=str(m["source"]),
                                target=str(m["target"]),
                                read_only=bool(m.get("read_only", True)),
                            )
                            for m in item.get("mounts", [])
                        ],
                    )
                    for item in data.get("agents", [])
                ]
            else:
                agents = []

            disable_home_access_check = (
                bool(data.get("disable_home_access_check", False)) if raw else False
            )
            config = cls(
                path=path,
                agents=agents,
                disable_home_access_check=disable_home_access_check,
                _fh=fh,
            )
            try:
                yield config
                if config._dirty:
                    config.save()
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
