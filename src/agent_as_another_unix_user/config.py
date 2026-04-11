from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
import fcntl
import json
import os
import sys
import time
import tomllib
from typing import Iterator, TextIO

from click import style

DEFAULT_CONFIG_FILENAME = "agent-as-another-unix-user.toml"
DEFAULT_GROUP_PREFIX = "su-as-"


def default_config_path() -> Path:
    return Path.home() / ".config" / DEFAULT_CONFIG_FILENAME


@dataclass(slots=True)
class AgentConfig:
    user_name: str
    su_as_agent_group: str
    entrypoint: str


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
                    "",
                ]
            )
        return "\n".join(lines).rstrip() + ("\n" if lines else "")

    def save(self) -> None:
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
                        spinner = _LockSpinner(path)
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


class _LockSpinner:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._started = False
        self._frames = "|/-\\"
        self._index = 0
        self._message = f"Waiting for lock on {style(path, fg='yellow')} "
        self._width = len(self._message) + 1

    def tick(self) -> None:
        frame = self._frames[self._index % len(self._frames)]
        self._index += 1
        if not self._started:
            self._started = True
        sys.stderr.write(f"\r{self._message}{style(frame, fg='green')}")
        sys.stderr.flush()

    def stop(self) -> None:
        if not self._started:
            return
        sys.stderr.write("\r" + (" " * self._width) + "\r")
        sys.stderr.flush()
