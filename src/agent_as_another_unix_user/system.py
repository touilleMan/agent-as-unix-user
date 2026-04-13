from __future__ import annotations

from hashlib import sha256
from dataclasses import dataclass
from pathlib import Path
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


def expected_su_as_agent_group(user_name: str) -> str:
    return f"su-as-{user_name}"


def expected_home(user_name: str, home_root: Path = Path("/home")) -> Path:
    return home_root / user_name


def _user_exists(runner: CommandRunner, user_name: str) -> tuple[bool, dict[str, str]]:
    result = runner.run(
        ["getent", "passwd", user_name],
        capture_output=True,
        text=True,
        check=False,
        quiet=True,
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


def _group_exists(runner: CommandRunner, group_name: str) -> bool:
    result = runner.run(
        ["getent", "group", group_name],
        capture_output=True,
        text=True,
        check=False,
        quiet=True,
    )
    return result.returncode == 0 and bool((result.stdout or "").strip())


def _current_user_groups(runner: CommandRunner) -> set[str]:
    result = runner.run(
        ["id", "-nG"], capture_output=True, text=True, check=False, quiet=True
    )
    if result.returncode != 0:
        return set()
    return set((result.stdout or "").split())


def resolve_agent_home(user_name: str) -> Path | None:
    tild_agent_home = f"~{user_name}"
    agent_home_str = os.path.expanduser(tild_agent_home)
    if agent_home_str == tild_agent_home:
        # Agent's home doesn't exist
        return None
    else:
        agent_home = Path(agent_home_str)
        if agent_home.exists():
            return agent_home
        else:
            return None


def acl_supported(runner: CommandRunner) -> bool:
    result = runner.run(
        ["setfacl", "--version"],
        capture_output=True,
        text=True,
        check=False,
        quiet=True,
    )
    return result.returncode == 0


def _read_default_acl(runner: CommandRunner, path: Path) -> str:
    result = runner.run(
        ["getfacl", "-p", "-d", str(path)],
        capture_output=True,
        text=True,
        check=False,
        quiet=True,
    )
    if result.returncode != 0:
        return ""
    return result.stdout or ""


def _has_setgid(path: Path) -> bool:
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return False
    return bool(mode & 0o2000)


def _human_home_has_acl_execute(runner: CommandRunner, user_name: str) -> bool:
    """Check if the human's home directory has an ACL execute entry for *user_name*."""
    human_home = Path.home()
    result = runner.run(
        ["getfacl", "--absolute-names", str(human_home)],
        capture_output=True,
        text=True,
        check=False,
        quiet=True,
    )
    if result.returncode != 0:
        return False
    assert isinstance(result.stdout, str)
    for line in result.stdout.splitlines():
        # e.g. "user:agent:--x"
        if line.strip() == f"user:{user_name}:--x":
            return True
    return False


def healthcheck_agent(runner: CommandRunner, agent: AgentConfig) -> HealthCheckResult:
    errors: list[str] = []
    home = resolve_agent_home(agent.user_name)

    user_ok, user_info = _user_exists(runner, agent.user_name)
    if not user_ok:
        errors.append("missing UNIX user")

    if not _group_exists(runner, agent.su_as_agent_group):
        errors.append("missing UNIX group")

    if home is None or not home.exists():
        errors.append(f"missing home directory: {home}")
    else:
        if _has_setgid(home):
            errors.append("home directory missing setgid bit")

        default_acl = _read_default_acl(runner, home)
        if not default_acl:
            errors.append("missing default ACL configuration or ACL unsupported")
        elif "default:" not in default_acl:
            errors.append("default ACL is not configured")

    entrypoint = Path(agent.entrypoint)
    if not entrypoint.exists():
        errors.append(f"missing entrypoint: {entrypoint}")
    elif not os.access(entrypoint, os.X_OK):
        errors.append(f"entrypoint is not executable: {entrypoint}")
    # TODO: check that entrypoint is owned by root
    # TODO: check that entrypoint has setuid bit set

    groups = _current_user_groups(runner)
    if agent.su_as_agent_group not in groups:
        errors.append(f"current user is not a member of {agent.su_as_agent_group}")

    if not acl_supported(runner):
        errors.append("ACL is not supported on this system")

    if not _human_home_has_acl_execute(runner, agent.user_name):
        errors.append(
            f"human home directory missing ACL execute for {agent.user_name} "
            f"(expected user:{agent.user_name}:--x on {Path.home()})"
        )

    if user_ok and not user_info.get("home"):
        errors.append("user account has no home directory configured")

    return HealthCheckResult(
        user_name=agent.user_name,
        home=str(home),
        errors=errors,
    )


def compute_sha256_fingerprint(data: bytes) -> str:
    return sha256(data).digest().hex()


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
#define _GNU_SOURCE  // Needed for setresgid/setresuid
#include <errno.h>
#include <grp.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

#ifndef TARGET_UID
// `TARGET_UID` is defined by the Makefile
#error TARGET_UID is not defined (compiling without the Makefile ?)
#endif

#ifndef TARGET_GID
// `TARGET_GID` is defined by the Makefile
#error TARGET_GID is not defined (compiling without the Makefile ?)
#endif

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <command> [args...]\\n", argv[0]);
        return 2;
    }

    // Sanity check to ensure the caller hasn't forget to scrub his environ variable before calling us
    if (getenv("USER") != NULL) {
        fprintf(stderr, "ERROR: environ variables haven't been scrub — aborting!\\n");
        return 1;
    }

    uid_t original_uid = getuid();

    // The binary is setuid-root, so euid is 0.
    // First, become fully root so we can manipulate groups and identities.
    if (setuid(0) != 0) {
        perror("setuid(0)");
        return 1;
    }

    // Drop all supplementary groups inherited from the calling user
    // This command requires to be root since removing a group can
    // cause privilege escalation (typically if a group is used to
    // retrain instead of give access).
    if (setgroups(0, NULL) != 0) {
        perror("setgroups");
        return 1;
    }

    // Permanently set GID to the agent's group
    if (setresgid(TARGET_GID, TARGET_GID, TARGET_GID) != 0) {
        perror("setresgid");
        return 1;
    }

    // Permanently set UID to the agent user (this also drops root)
    if (setresuid(TARGET_UID, TARGET_UID, TARGET_UID) != 0) {
        perror("setresuid");
        return 1;
    }

    // Sanity check: ensure we cannot regain root
    if (setuid(0) != -1) {
        fprintf(stderr, "ERROR: was able to regain root access — aborting!\\n");
        return 1;
    }

    // Sanity check: ensure we cannot get back the original caller UID
    if (setuid(original_uid) != -1) {
        fprintf(stderr, "ERROR: was able to regain original user access — aborting!\\n");
        return 1;
    }

    execvp(argv[1], &argv[1]);
    perror("execv");
    return 1;
}
"""


def entrypoint_src_makefile(target_uid: str, target_gid: str) -> str:
    return f"""\
CC ?= cc
CFLAGS ?= -O2 -Wall -Wextra -Werror
TARGET_UID ?= {target_uid}
TARGET_GID ?= {target_gid}

all: su_as_agent

su_as_agent: main.c
	$(CC) $(CFLAGS) -DTARGET_UID=$(TARGET_UID) -DTARGET_GID=$(TARGET_GID) -o $@ $<
"""
