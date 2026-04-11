from __future__ import annotations

from pathlib import Path
import json
import tomllib

from .models import AgentConfig

DEFAULT_CONFIG_FILENAME = "agent-as-another-unix-user.toml"
DEFAULT_GROUP_PREFIX = "su-as-"


def default_config_path() -> Path:
    return Path.home() / ".config" / DEFAULT_CONFIG_FILENAME


def load_config(path: Path) -> list[AgentConfig]:
    if not path.exists():
        return []

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
    return agents


def save_config(path: Path, agents: list[AgentConfig]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for agent in agents:
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


def upsert_agent(path: Path, agent: AgentConfig) -> list[AgentConfig]:
    agents = load_config(path)
    for idx, existing in enumerate(agents):
        if existing.user_name == agent.user_name:
            agents[idx] = agent
            save_config(path, agents)
            return agents
    agents.append(agent)
    save_config(path, agents)
    return agents


def remove_agent(path: Path, user_name: str) -> list[AgentConfig]:
    agents = load_config(path)
    filtered = [agent for agent in agents if agent.user_name != user_name]
    if path.exists() or filtered:
        save_config(path, filtered)
    return filtered


def get_agent(path: Path, user_name: str) -> AgentConfig | None:
    for agent in load_config(path):
        if agent.user_name == user_name:
            return agent
    return None
