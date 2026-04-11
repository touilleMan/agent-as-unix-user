from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import getpass
import os

import click

from .config import AgentConfig
from .runner import CommandRunner


@dataclass(slots=True)
class HealthCheckResult:
    user_name: str
    home: str
    status: str
    reasons: list[str]


def current_user_name() -> str:
    return os.environ.get("SUDO_USER") or getpass.getuser()


def expected_group_name(user_name: str) -> str:
    return f"su-as-{user_name}"


def expected_home(user_name: str, home_root: Path = Path("/home")) -> Path:
    return home_root / user_name


def user_exists(runner: CommandRunner, user_name: str) -> tuple[bool, dict[str, str]]:
    result = runner.run(
        ["getent", "passwd", user_name], capture_output=True, text=True, check=False
    )
    if result.returncode != 0 or not (result.stdout or "").strip():
        return False, {}
    fields = (result.stdout or "").strip().split(":")
    info = {
        "user_name": fields[0] if len(fields) > 0 else user_name,
        "uid": fields[2] if len(fields) > 2 else "",
        "gid": fields[3] if len(fields) > 3 else "",
        "home": fields[5] if len(fields) > 5 else str(expected_home(user_name)),
        "shell": fields[6] if len(fields) > 6 else "",
    }
    return True, info


def group_exists(runner: CommandRunner, group_name: str) -> bool:
    result = runner.run(
        ["getent", "group", group_name], capture_output=True, text=True, check=False
    )
    return result.returncode == 0 and bool((result.stdout or "").strip())


def current_user_groups(runner: CommandRunner) -> set[str]:
    result = runner.run(["id", "-nG"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return set()
    return set((result.stdout or "").split())


def resolve_agent_home(runner: CommandRunner, user_name: str) -> Path:
    ok, info = user_exists(runner, user_name)
    if ok and info.get("home"):
        return Path(info["home"])
    return expected_home(user_name)


def acl_supported(runner: CommandRunner) -> bool:
    result = runner.run(
        ["setfacl", "--version"], capture_output=True, text=True, check=False
    )
    return result.returncode == 0


def read_default_acl(runner: CommandRunner, path: Path) -> str:
    result = runner.run(
        ["getfacl", "-p", "-d", str(path)], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        return ""
    return result.stdout or ""


def has_setgid(path: Path) -> bool:
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return False
    return bool(mode & 0o2000)


def healthcheck_agent(runner: CommandRunner, agent: AgentConfig) -> HealthCheckResult:
    reasons: list[str] = []
    home = resolve_agent_home(runner, agent.user_name)

    user_ok, user_info = user_exists(runner, agent.user_name)
    if not user_ok:
        reasons.append("missing UNIX user")

    if not group_exists(runner, agent.su_as_agent_group):
        reasons.append("missing UNIX group")

    if not home.exists():
        reasons.append(f"missing home directory: {home}")

    if home.exists() and not has_setgid(home):
        reasons.append("home directory missing setgid bit")

    default_acl = read_default_acl(runner, home) if home.exists() else ""
    if not default_acl:
        reasons.append("missing default ACL configuration or ACL unsupported")
    elif "default:" not in default_acl:
        reasons.append("default ACL is not configured")

    entrypoint = Path(agent.entrypoint)
    if not entrypoint.exists():
        reasons.append(f"missing entrypoint: {entrypoint}")
    elif not os.access(entrypoint, os.X_OK):
        reasons.append(f"entrypoint is not executable: {entrypoint}")

    groups = current_user_groups(runner)
    if agent.su_as_agent_group not in groups:
        reasons.append(f"current user is not a member of {agent.su_as_agent_group}")

    if not acl_supported(runner):
        reasons.append("ACL is not supported on this system")

    if user_ok and not user_info.get("home"):
        reasons.append("user account has no home directory configured")

    status = "ok" if not reasons else "broken"
    return HealthCheckResult(
        user_name=agent.user_name,
        home=str(home),
        status=status,
        reasons=reasons,
    )


def require_root(is_root: bool) -> None:
    if not is_root:
        raise click.ClickException("this command requires root privileges")


def agent_source_dir(home: Path) -> Path:
    return home / ".config" / "agent-as-another-unix-user" / "su_as_agent-src"


def agent_readme_content(agent: AgentConfig, config_path: Path, home: Path) -> str:
    return f"""# Agent home for {agent.user_name}

This directory belongs to the agent user `{agent.user_name}`.

Related configuration file:
- `{config_path}`

Entrypoint:
- `{home / "su_as_agent"}`

Source code:
- `{agent_source_dir(home)}`

To delete this agent safely, use `au delete --user {agent.user_name}`.
That command will remove the UNIX user, group, home directory and all data.
"""


def agent_main_c(target_uid: str) -> str:
    return f"""#include <errno.h>\n#include <stdio.h>\n#include <stdlib.h>\n#include <unistd.h>\n\n#ifndef TARGET_UID\n#define TARGET_UID {target_uid}\n#endif\n\nint main(int argc, char **argv) {{\n    if (argc < 2) {{\n        fprintf(stderr, \"usage: %s <command> [args...]\\n\", argv[0]);\n        return 2;\n    }}\n    if (setuid(TARGET_UID) != 0) {{\n        perror(\"setuid\");\n        return 1;\n    }}\n    execvp(argv[1], &argv[1]);\n    perror(\"execvp\");\n    return 1;\n}}\n"""


def agent_makefile(target_uid: str) -> str:
    return f"""CC ?= cc\nCFLAGS ?= -O2 -Wall -Wextra\nTARGET_UID ?= {target_uid}\n\nall: su_as_agent\n\nsu_as_agent: main.c\n\t$(CC) $(CFLAGS) -DTARGET_UID=$(TARGET_UID) -o $@ $<\n"""
