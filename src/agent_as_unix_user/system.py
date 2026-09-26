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


def _read_acl(runner: CommandRunner, path: Path) -> str:
    result = runner.run(
        ["getfacl", "-p", str(path)],
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
        if not _has_setgid(home):
            errors.append("home directory missing setgid bit")

        acl = _read_acl(runner, home)
        expected_default_group_acl = f"default:group:{agent.su_as_agent_group}:rwx"
        if not acl:
            errors.append("missing default ACL configuration or ACL unsupported")
        elif expected_default_group_acl not in acl.splitlines():
            errors.append("default ACL is not configured")

    entrypoint = Path(agent.entrypoint)
    if not entrypoint.exists():
        errors.append(f"missing entrypoint: {entrypoint}")
    elif not os.access(entrypoint, os.X_OK):
        errors.append(f"entrypoint is not executable: {entrypoint}")
    elif is_entrypoint_inside_home(entrypoint, home):
        # Legacy agents kept the setuid entrypoint inside the agent's home,
        # where the agent could swap it out. Recreate the agent to relocate it.
        errors.append(
            f"entrypoint is inside the agent home ({entrypoint}); "
            "it is writable by the agent — recreate this agent with `au new`"
        )
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


def config_dir(home: Path) -> Path:
    return home / ".config" / "agent-as-unix-user"


# Root-owned location for the setuid entrypoint. It is *outside* the agent's
# home on purpose: the agent must never be able to modify (or swap out) the
# binary it invokes with root privileges. Every directory on this path is
# owned by root and only writable by root.
ENTRYPOINT_INSTALL_ROOT = Path("/usr/local/libexec/agent-as-unix-user")


def entrypoint_install_dir(user_name: str) -> Path:
    return ENTRYPOINT_INSTALL_ROOT / user_name


def entrypoint_install_path(user_name: str) -> Path:
    return entrypoint_install_dir(user_name) / "su_as_agent"


def is_entrypoint_inside_home(entrypoint: Path, home: Path | None) -> bool:
    """True if the entrypoint lives inside the agent's home (insecure legacy layout)."""
    if home is None:
        return False
    try:
        entrypoint.resolve().relative_to(home.resolve())
        return True
    except (ValueError, OSError):
        return False


def agent_readme_content(agent: AgentConfig, config_path: Path, home: Path) -> str:
    return f"""# Agent home for {agent.user_name}

This directory belongs to the agent user `{agent.user_name}`.

Related configuration file:
- `{config_path}`

Entrypoint (root-owned, outside this home):
- `{entrypoint_install_path(agent.user_name)}`

To delete this agent safely, use `au delete --user {agent.user_name}`.
That command will remove the UNIX user, group, home directory and all data.
"""


ENTRYPOINT_SRC_MAIN_C = """
#define _GNU_SOURCE  // Needed for setresgid/setresuid, unshare, O_PATH
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <linux/limits.h>
#include <linux/openat2.h>
#include <pwd.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>

#ifndef TARGET_UID
// `TARGET_UID` is defined by the Makefile
#error TARGET_UID is not defined (compiling without the Makefile ?)
#endif

#ifndef TARGET_GID
// `TARGET_GID` is defined by the Makefile
#error TARGET_GID is not defined (compiling without the Makefile ?)
#endif

#ifndef CALLER_UID
// `CALLER_UID` is defined by the Makefile (the uid of the human who created
// the agent, and the only uid allowed to run this entrypoint).
#error CALLER_UID is not defined (compiling without the Makefile ?)
#endif

#define MAX_MOUNTS 64

struct mount_entry {
    const char *source;
    const char *target;
    int read_only;
};

static int path_starts_with(const char *path, const char *prefix) {
    size_t len = strlen(prefix);
    if (strncmp(path, prefix, len) != 0)
        return 0;
    return path[len] == '\\0' || path[len] == '/';
}

// Open a single path component `name` relative to `dir_fd` as an O_PATH
// directory fd, refusing to follow symlinks and refusing to escape `dir_fd`.
// A symlink component fails with ELOOP rather than being followed — this is
// what stops a caller from redirecting a mount target out of the agent's home.
static int open_dir_beneath(int dir_fd, const char *name) {
    struct open_how how;
    memset(&how, 0, sizeof(how));
    how.flags = O_PATH | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW;
    how.resolve = RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS;
    return (int)syscall(SYS_openat2, dir_fd, name, &how, sizeof(how));
}

// Resolve `rel` (a relative path) beneath `base_fd`, creating any missing
// directory components as directories owned by the agent. Every component must
// be a real directory: a symlink anywhere on the path is refused. Returns an
// O_PATH fd to the leaf directory, or -1 with errno set. Caller closes the fd.
static int resolve_beneath_create(int base_fd, const char *rel) {
    char buf[PATH_MAX];
    size_t len = strlen(rel);
    if (len >= sizeof(buf)) { errno = ENAMETOOLONG; return -1; }
    memcpy(buf, rel, len + 1);

    int cur = dup(base_fd);
    if (cur < 0) return -1;

    char *saveptr = NULL;
    for (char *tok = strtok_r(buf, "/", &saveptr); tok != NULL; ) {
        char *next = strtok_r(NULL, "/", &saveptr);

        // Reject "." and ".." (RESOLVE_BENEATH also blocks "..").
        if (strcmp(tok, ".") == 0 || strcmp(tok, "..") == 0) {
            close(cur);
            errno = EINVAL;
            return -1;
        }

        int fd = open_dir_beneath(cur, tok);
        if (fd < 0 && errno == ENOENT) {
            // Relative to `cur` (a real directory fd), so this can't cross a symlink.
            if (mkdirat(cur, tok, 0755) != 0) { close(cur); return -1; }
            if (fchownat(cur, tok, TARGET_UID, TARGET_GID, AT_SYMLINK_NOFOLLOW) != 0) {
                close(cur);
                return -1;
            }
            fd = open_dir_beneath(cur, tok);
        }
        if (fd < 0) { close(cur); return -1; }

        close(cur);
        cur = fd;
        tok = next;
    }
    return cur;
}

int main(int argc, char **argv) {
    // Parse arguments
    struct mount_entry mounts[MAX_MOUNTS];
    int mount_count = 0;
    int cmd_start = -1;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--") == 0) {
            cmd_start = i + 1;
            break;
        }
        if (strcmp(argv[i], "--mount-ro") == 0 || strcmp(argv[i], "--mount-rw") == 0) {
            int is_ro = (strcmp(argv[i], "--mount-ro") == 0);
            if (i + 2 >= argc) {
                fprintf(stderr, "ERROR: %s requires two arguments (source and target)\\n", argv[i]);
                return 2;
            }
            if (mount_count >= MAX_MOUNTS) {
                fprintf(stderr, "ERROR: too many mounts (max %d)\\n", MAX_MOUNTS);
                return 2;
            }
            mounts[mount_count].source = argv[i + 1];
            mounts[mount_count].target = argv[i + 2];
            mounts[mount_count].read_only = is_ro;
            mount_count++;
            i += 2;
        } else {
            fprintf(stderr, "ERROR: unexpected argument before '--': %s\\n", argv[i]);
            return 2;
        }
    }

    if (cmd_start < 0 || cmd_start >= argc) {
        fprintf(stderr, "usage: %s [--mount-ro source target] [--mount-rw source target]... -- <command> [args...]\\n", argv[0]);
        return 2;
    }

    // Sanity check to ensure the caller hasn't forget to scrub his environ variable before calling us
    if (getenv("USER") != NULL) {
        fprintf(stderr, "ERROR: environ variables haven't been scrub, aborting!\\n");
        return 1;
    }

    uid_t original_uid = getuid();

    // Only the human who created this agent may run the entrypoint.
    // The binary is setuid-root and group-executable by the su-as-agent group,
    // which is the agent's *primary* group. Without this check the agent could
    // execute the entrypoint itself and abuse its root privileges to escalate.
    if (original_uid != CALLER_UID) {
        fprintf(stderr,
                "ERROR: this entrypoint may only be run by uid %d (called by uid %d)\\n",
                CALLER_UID, original_uid);
        return 1;
    }

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

    // Open the agent's home as an O_PATH anchor for resolving mount targets.
    // Every target is resolved *beneath* this fd with symlinks refused, so a
    // caller cannot redirect a target outside the home via a planted symlink.
    int home_fd = -1;
    if (mount_count > 0) {
        home_fd = open(agent_home, O_PATH | O_DIRECTORY | O_CLOEXEC);
        if (home_fd < 0) {
            fprintf(stderr, "ERROR: cannot open agent home %s: %s\\n",
                    agent_home, strerror(errno));
            return 1;
        }
    }

    // Validate and perform bind mounts
    for (int i = 0; i < mount_count; i++) {
        // --- Source ---
        // Open and validate through the fd, so the checks and the mount refer to
        // the same inode (no check/use gap). Symlinks are followed: the checks
        // below run on the resolved inode (owned by the caller, under their
        // home), so a link is safe and links within the home keep working.
        int src_fd = open(mounts[i].source, O_PATH | O_CLOEXEC);
        if (src_fd < 0) {
            fprintf(stderr, "ERROR: cannot open mount source %s: %s\\n",
                    mounts[i].source, strerror(errno));
            return 1;
        }

        char src_fd_path[64];
        snprintf(src_fd_path, sizeof(src_fd_path), "/proc/self/fd/%d", src_fd);

        // Real path of the opened inode, for the checks below and error messages.
        char real_source[PATH_MAX];
        ssize_t n = readlink(src_fd_path, real_source, sizeof(real_source) - 1);
        if (n < 0) {
            fprintf(stderr, "ERROR: cannot resolve mount source %s: %s\\n",
                    mounts[i].source, strerror(errno));
            return 1;
        }
        real_source[n] = '\\0';

        struct stat src_stat;
        if (fstat(src_fd, &src_stat) != 0) {
            fprintf(stderr, "ERROR: cannot stat mount source %s: %s\\n",
                    real_source, strerror(errno));
            return 1;
        }

        // The source must be a directory the caller owns, under their home.
        if (!S_ISDIR(src_stat.st_mode)) {
            fprintf(stderr, "ERROR: mount source %s is not a directory\\n",
                    real_source);
            return 1;
        }
        if (!path_starts_with(real_source, caller_home)) {
            fprintf(stderr, "ERROR: mount source %s is not under caller's home %s\\n",
                    real_source, caller_home);
            return 1;
        }
        if (src_stat.st_uid != original_uid) {
            fprintf(stderr, "ERROR: mount source %s is not owned by caller "
                    "(uid %d, expected %d)\\n",
                    real_source, src_stat.st_uid, original_uid);
            return 1;
        }

        // --- Target ---
        // This only derives the home-relative path; the protection is resolving
        // it beneath home_fd with symlinks refused (below).
        if (!path_starts_with(mounts[i].target, agent_home)) {
            fprintf(stderr, "ERROR: mount target %s is not under agent's home %s\\n",
                    mounts[i].target, agent_home);
            return 1;
        }
        const char *rel = mounts[i].target + strlen(agent_home);
        while (*rel == '/') rel++;

        int tgt_fd = resolve_beneath_create(home_fd, rel);
        if (tgt_fd < 0) {
            fprintf(stderr, "ERROR: cannot create mount target %s: %s\\n",
                    mounts[i].target, strerror(errno));
            return 1;
        }

        char tgt_fd_path[64];
        snprintf(tgt_fd_path, sizeof(tgt_fd_path), "/proc/self/fd/%d", tgt_fd);

        // --- Bind mount, referring to both inodes through /proc/self/fd so the
        // resolved source/target cannot be swapped between check and mount. ---
        if (mount(src_fd_path, tgt_fd_path, NULL, MS_BIND, NULL) != 0) {
            fprintf(stderr, "ERROR: bind mount %s -> %s failed: %s\\n",
                    real_source, mounts[i].target, strerror(errno));
            return 1;
        }
        // A bind ignores nosuid/nodev/ro on the initial mount, so apply them via
        // remount. The remount must hit the new mount, but `tgt_fd` predates it
        // and refers to the underlying dir (remounting through it gives EINVAL);
        // re-resolving crosses into the mount. `tgt_fd` stays open to pin the path.
        int mnt_fd = resolve_beneath_create(home_fd, rel);
        if (mnt_fd < 0) {
            fprintf(stderr, "ERROR: cannot reopen mount target %s: %s\\n",
                    mounts[i].target, strerror(errno));
            return 1;
        }
        char mnt_fd_path[64];
        snprintf(mnt_fd_path, sizeof(mnt_fd_path), "/proc/self/fd/%d", mnt_fd);

        unsigned long remount_flags = MS_REMOUNT | MS_BIND | MS_NOSUID | MS_NODEV;
        if (mounts[i].read_only)
            remount_flags |= MS_RDONLY;
        if (mount(NULL, mnt_fd_path, NULL, remount_flags, NULL) != 0) {
            fprintf(stderr, "ERROR: remount (%s) of %s failed: %s\\n",
                    mounts[i].read_only ? "ro,nosuid,nodev" : "nosuid,nodev",
                    mounts[i].target, strerror(errno));
            return 1;
        }

        close(mnt_fd);
        close(src_fd);
        close(tgt_fd);
    }
    if (home_fd >= 0)
        close(home_fd);

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

    if (setenv("HOME", agent_home, 1) != 0) {
        perror("setenv(HOME)");
        return 1;
    }

    if (chdir(agent_home) != 0) {
        perror("chdir(agent_home)");
        return 1;
    }

    execvp(argv[cmd_start], &argv[cmd_start]);
    perror("execv");
    return 1;
}
"""


def entrypoint_src_makefile(target_uid: str, target_gid: str, caller_uid: str) -> str:
    return f"""\
CC ?= cc
CFLAGS ?= -O2 -Wall -Wextra -Werror
TARGET_UID ?= {target_uid}
TARGET_GID ?= {target_gid}
CALLER_UID ?= {caller_uid}

all: su_as_agent

su_as_agent: main.c
	$(CC) $(CFLAGS) -DTARGET_UID=$(TARGET_UID) -DTARGET_GID=$(TARGET_GID) -DCALLER_UID=$(CALLER_UID) -o $@ $<
"""
