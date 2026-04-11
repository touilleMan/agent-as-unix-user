from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import getpass
import os


from .config import AgentConfig
from .runner import CommandRunner


@dataclass(slots=True)
class HealthCheckResult:
    user_name: str
    home: str
    errors: list[str]

    @property
    def is_ok(self) -> bool:
        return not self.errors


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
    errors: list[str] = []
    home = resolve_agent_home(runner, agent.user_name)

    user_ok, user_info = user_exists(runner, agent.user_name)
    if not user_ok:
        errors.append("missing UNIX user")

    if not group_exists(runner, agent.su_as_agent_group):
        errors.append("missing UNIX group")

    if not home.exists():
        errors.append(f"missing home directory: {home}")

    if home.exists() and not has_setgid(home):
        errors.append("home directory missing setgid bit")

    default_acl = read_default_acl(runner, home) if home.exists() else ""
    if not default_acl:
        errors.append("missing default ACL configuration or ACL unsupported")
    elif "default:" not in default_acl:
        errors.append("default ACL is not configured")

    entrypoint = Path(agent.entrypoint)
    if not entrypoint.exists():
        errors.append(f"missing entrypoint: {entrypoint}")
    elif not os.access(entrypoint, os.X_OK):
        errors.append(f"entrypoint is not executable: {entrypoint}")

    groups = current_user_groups(runner)
    if agent.su_as_agent_group not in groups:
        errors.append(f"current user is not a member of {agent.su_as_agent_group}")

    if not acl_supported(runner):
        errors.append("ACL is not supported on this system")

    if user_ok and not user_info.get("home"):
        errors.append("user account has no home directory configured")

    return HealthCheckResult(
        user_name=agent.user_name,
        home=str(home),
        errors=errors,
    )


def entrypoint_src_dir(home: Path) -> Path:
    return home / ".config" / "agent-as-another-unix-user" / "su_as_agent-src"


def agent_readme_content(agent: AgentConfig, config_path: Path, home: Path) -> str:
    return f"""# Agent home for {agent.user_name}

This directory belongs to the agent user `{agent.user_name}`.

Related configuration file:
- `{config_path}`

Entrypoint:
- `{home / "su_as_agent"}`

Source code:
- `{entrypoint_src_dir(home)}`

To delete this agent safely, use `au delete --user {agent.user_name}`.
That command will remove the UNIX user, group, home directory and all data.
"""


ENTRYPOINT_SRC_MAIN_C = """
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

#ifndef TARGET_UID
// `TARGET_UID` is defined by the Makefile
#error TARGET_UID is not defined (compiling without the Makefile ?)
#endif

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <command> [args...]\n", argv[0]);
        return 2;
    }
    if (setuid(TARGET_UID) != 0) {
        perror("setuid");
        return 1;
    }
    execvp(argv[1], &argv[1]);
    perror("execvp");
    return 1;
}
"""


def entrypoint_src_makefile(target_uid: str) -> str:
    return f"""\
CC ?= cc
CFLAGS ?= -O2 -Wall -Wextra
TARGET_UID ?= {target_uid}

all: su_as_agent

su_as_agent: main.c
	$(CC) $(CFLAGS) -DTARGET_UID=$(TARGET_UID) -o $@ $<
"""
