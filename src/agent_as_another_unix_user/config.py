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
class AgentConfig:
    user_name: str
    "UNIX user name"
    su_as_agent_group: str
    "UNIX group to be able to use the entrypoint and read/write the agent home"
    entrypoint: str
    "Path to the binary to execute to run command as the agent user"
    bootstrapped: bool
    """
    If the agent has been fully configured (UNIX user, group etc.).

    Might be `False` if new agent command couldn't finish...
    """
    acl_external_accesses: list[str]
    """
    We track the ACL rights that have been given to this agent here since the
    OS doesn't provide a centralized way to get this info.

    Indeed: ACL rights are set as extended attribute in the file system, so
    in theory we should do a full scan of the filesystem to find all the
    files/folders that have ACL for our agent UID/GID.

    On top of that it is important to remove those ACL rights once the agent
    user has been deleted to avoid vulnerabilities related to UID recycling (
    i.e. a newly created user might get the UID of our deleted user and hence
    is able to use the ACL we forgot to remove).

    Of course the tracking we do here only concern the ACL that have been set
    through our own commands however this seems like a good enough security
    as long as the end-user is aware he shouldn't be playing with ACL on his own.
    """


@dataclass(slots=True)
class Config:
    path: Path
    agents: list[AgentConfig] = field(default_factory=list)
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
        for agent in self.agents:
            lines.extend(
                [
                    "[[agents]]",
                    f"user_name = {json.dumps(agent.user_name)}",
                    f"su_as_agent_group = {json.dumps(agent.su_as_agent_group)}",
                    f"entrypoint = {json.dumps(agent.entrypoint)}",
                    f"bootstrapped = {json.dumps(agent.bootstrapped)}",
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
            if raw:
                data = tomllib.loads(raw)
                agents = [
                    AgentConfig(
                        user_name=str(item["user_name"]),
                        su_as_agent_group=str(item["su_as_agent_group"]),
                        entrypoint=str(item["entrypoint"]),
                        bootstrapped=bool(item["bootstrapped"]),
                        acl_external_accesses=[
                            str(a) for a in item.get("acl_external_accesses", [])
                        ],
                    )
                    for item in data.get("agents", [])
                ]
            else:
                agents = []

            config = cls(path=path, agents=agents, _fh=fh)
            try:
                yield config
                if config._dirty:
                    config.save()
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
