from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import tomllib

DEFAULT_CONFIG_FILENAME = "agent-as-another-unix-user.toml"
DEFAULT_GROUP_PREFIX = "su-as-"


@dataclass(slots=True)
class AgentConfig:
    user_name: str
    su_as_agent_group: str
    entrypoint: str


@dataclass(slots=True)
class Config:
    agents: list[AgentConfig]


def default_config_path() -> Path:
    return Path.home() / ".config" / DEFAULT_CONFIG_FILENAME


def load_config(path: Path) -> Config:
    if not path.exists():
        return Config(agents=[])

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    agents: list[AgentConfig] = []
    for raw in data.get("agents", []):
        agents.append(
            AgentConfig(
                user_name=str(raw["user_name"]),
                su_as_agent_group=str(raw["su_as_agent_group"]),
                entrypoint=str(raw["entrypoint"]),
            )
        )
    return Config(agents=agents)


def save_config(path: Path, config: Config) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for agent in config.agents:
        lines.extend(
            [
                "[[agents]]",
                f"user_name = {json.dumps(agent.user_name)}",
                f"su_as_agent_group = {json.dumps(agent.su_as_agent_group)}",
                f"entrypoint = {json.dumps(agent.entrypoint)}",
                "",
            ]
        )
    content = "\n".join(lines).rstrip() + ("\n" if lines else "")
    path.write_text(content, encoding="utf-8")


def upsert_agent(path: Path, agent: AgentConfig) -> None:
    config = load_config(path)
    for idx, existing in enumerate(config.agents):
        if existing.user_name == agent.user_name:
            config.agents[idx] = agent
            save_config(path, config)
    config.agents.append(agent)
    save_config(path, config)


def remove_agent(path: Path, user_name: str) -> None:
    config = load_config(path)
    filtered = [agent for agent in config.agents if agent.user_name != user_name]
    if path.exists() or filtered:
        config.agents = filtered
        save_config(path, config)


def get_agent(path: Path, user_name: str) -> AgentConfig | None:
    for agent in load_config(path).agents:
        if agent.user_name == user_name:
            return agent
    return None
