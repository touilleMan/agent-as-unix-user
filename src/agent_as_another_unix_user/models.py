from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class AgentConfig:
    user_name: str
    su_as_agent_group: str
    entrypoint: str


@dataclass(slots=True)
class HealthCheckResult:
    user_name: str
    home: str
    status: str
    reasons: list[str]
