from pathlib import Path

from agent_as_another_unix_user.config import AgentConfig, Config


def load_config(path: Path) -> Config:
    with Config.open(path) as config:
        return Config(path=path, agents=list(config.agents))


def save_config(path: Path, config: Config) -> None:
    with Config.open(path) as locked_config:
        locked_config.agents = list(config.agents)
        locked_config._dirty = True


def upsert_agent(path: Path, agent: AgentConfig) -> Config:
    with Config.open(path) as config:
        config.upsert_agent(agent)
        return Config(path=path, agents=list(config.agents))


def remove_agent(path: Path, user_name: str) -> Config:
    with Config.open(path) as config:
        config.remove_agent(user_name)
        return Config(path=path, agents=list(config.agents))


def get_agent(path: Path, user_name: str) -> AgentConfig | None:
    with Config.open(path) as config:
        return config.get_agent(user_name)
