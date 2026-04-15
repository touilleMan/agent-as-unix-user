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
#define _GNU_SOURCE  // Needed for setresgid/setresuid, unshare
#include <errno.h>
#include <grp.h>
#include <linux/limits.h>
#include <pwd.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <unistd.h>

#ifndef TARGET_UID
// `TARGET_UID` is defined by the Makefile
#error TARGET_UID is not defined (compiling without the Makefile ?)
#endif

#ifndef TARGET_GID
// `TARGET_GID` is defined by the Makefile
#error TARGET_GID is not defined (compiling without the Makefile ?)
#endif

#define MAX_MOUNTS 64

struct mount_entry {
    const char *source;
    const char *target;
};

static int path_starts_with(const char *path, const char *prefix) {
    size_t len = strlen(prefix);
    if (strncmp(path, prefix, len) != 0)
        return 0;
    return path[len] == '\\0' || path[len] == '/';
}

static int mkdir_p(const char *path, mode_t mode, uid_t uid, gid_t gid) {
    char tmp[PATH_MAX];
    size_t len = strlen(path);
    if (len >= sizeof(tmp)) { errno = ENAMETOOLONG; return -1; }
    memcpy(tmp, path, len + 1);
    if (len > 0 && tmp[len - 1] == '/') tmp[--len] = '\\0';
    for (char *p = tmp + 1; *p; p++) {
        if (*p == '/') {
            *p = '\\0';
            if (mkdir(tmp, mode) == 0) {
                if (chown(tmp, uid, gid) != 0) return -1;
            } else if (errno != EEXIST) {
                return -1;
            }
            *p = '/';
        }
    }
    if (mkdir(tmp, mode) == 0) {
        if (chown(tmp, uid, gid) != 0) return -1;
    } else if (errno != EEXIST) {
        return -1;
    }
    return 0;
}

int main(int argc, char **argv) {
    // Parse arguments: [--mount source target]... -- command [args...]
    struct mount_entry mounts[MAX_MOUNTS];
    int mount_count = 0;
    int cmd_start = -1;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--") == 0) {
            cmd_start = i + 1;
            break;
        }
        if (strcmp(argv[i], "--mount") == 0) {
            if (i + 2 >= argc) {
                fprintf(stderr, "ERROR: --mount requires two arguments (source and target)\\n");
                return 2;
            }
            if (mount_count >= MAX_MOUNTS) {
                fprintf(stderr, "ERROR: too many mounts (max %d)\\n", MAX_MOUNTS);
                return 2;
            }
            mounts[mount_count].source = argv[i + 1];
            mounts[mount_count].target = argv[i + 2];
            mount_count++;
            i += 2;
        } else {
            fprintf(stderr, "ERROR: unexpected argument before '--': %s\\n", argv[i]);
            return 2;
        }
    }

    if (cmd_start < 0 || cmd_start >= argc) {
        fprintf(stderr, "usage: %s [--mount source target]... -- <command> [args...]\\n", argv[0]);
        return 2;
    }

    // Sanity check to ensure the caller hasn't forget to scrub his environ variable before calling us
    if (getenv("USER") != NULL) {
        fprintf(stderr, "ERROR: environ variables haven't been scrub, aborting!\\n");
        return 1;
    }

    uid_t original_uid = getuid();

    // Resolve caller's and agent's home directories for mount validation
    struct passwd *caller_pw = getpwuid(original_uid);
    if (!caller_pw || !caller_pw->pw_dir) {
        fprintf(stderr, "ERROR: cannot determine caller's home directory\\n");
        return 1;
    }
    // Note `getpwuid` returned pointer shouldn't be freed, however its content
    // might get overwritten by a subsequent `getpwuid` call.
    char caller_home[PATH_MAX];
    snprintf(caller_home, sizeof(caller_home), "%s", caller_pw->pw_dir);

    struct passwd *agent_pw = getpwuid(TARGET_UID);
    if (!agent_pw || !agent_pw->pw_dir) {
        fprintf(stderr, "ERROR: cannot determine agent's home directory\\n");
        return 1;
    }
    char agent_home[PATH_MAX];
    snprintf(agent_home, sizeof(agent_home), "%s", agent_pw->pw_dir);

    // ------------- BECOMING ROOT --------------------

    // The binary is setuid-root, so euid is 0.
    // First, become fully root so we can manipulate groups and identities.
    if (setuid(0) != 0) {
        perror("setuid(0)");
        return 1;
    }

    // Create a private mount namespace so bind mounts are automatically
    // cleaned up when the process tree exits.
    if (mount_count > 0) {
        if (unshare(CLONE_NEWNS) != 0) {
            perror("unshare(CLONE_NEWNS)");
            return 1;
        }
        // Make all existing mounts private so nothing leaks out
        if (mount("none", "/", NULL, MS_REC | MS_PRIVATE, NULL) != 0) {
            perror("mount(MS_REC | MS_PRIVATE)");
            return 1;
        }
    }

    // Validate and perform bind mounts
    for (int i = 0; i < mount_count; i++) {
        // Resolve source to realpath
        char real_source[PATH_MAX];
        if (!realpath(mounts[i].source, real_source)) {
            fprintf(stderr, "ERROR: cannot resolve mount source: %s: %s\\n",
                    mounts[i].source, strerror(errno));
            return 1;
        }

        // Security check: source must be under the caller's home
        if (!path_starts_with(real_source, caller_home)) {
            fprintf(stderr, "ERROR: mount source %s is not under caller's home %s\\n",
                    real_source, caller_home);
            return 1;
        }

        // Security check: source must be owned by the caller
        struct stat src_stat;
        if (stat(real_source, &src_stat) != 0) {
            fprintf(stderr, "ERROR: cannot stat mount source %s: %s\\n",
                    real_source, strerror(errno));
            return 1;
        }
        if (src_stat.st_uid != original_uid) {
            fprintf(stderr, "ERROR: mount source %s is not owned by caller "
                    "(uid %d, expected %d)\\n",
                    real_source, src_stat.st_uid, original_uid);
            return 1;
        }

        // Security check: target must be under the agent's home
        if (!path_starts_with(mounts[i].target, agent_home)) {
            fprintf(stderr, "ERROR: mount target %s is not under agent's home %s\\n",
                    mounts[i].target, agent_home);
            return 1;
        }

        // Create the target directory
        if (mkdir_p(mounts[i].target, 0755, TARGET_UID, TARGET_GID) != 0) {
            fprintf(stderr, "ERROR: cannot create mount target %s: %s\\n",
                    mounts[i].target, strerror(errno));
            return 1;
        }

        // Bind mount (read-write initially, then remount read-only)
        if (mount(real_source, mounts[i].target, NULL, MS_BIND, NULL) != 0) {
            fprintf(stderr, "ERROR: bind mount %s -> %s failed: %s\\n",
                    real_source, mounts[i].target, strerror(errno));
            return 1;
        }
        if (mount(NULL, mounts[i].target, NULL,
                  MS_REMOUNT | MS_BIND | MS_RDONLY, NULL) != 0) {
            fprintf(stderr, "ERROR: remount read-only %s failed: %s\\n",
                    mounts[i].target, strerror(errno));
            return 1;
        }
    }

    // ------------- LEAVING ROOT, BECOMING AGENT --------------------

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

    execvp(argv[cmd_start], &argv[cmd_start]);
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
