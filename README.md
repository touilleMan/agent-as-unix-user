# A⁴U² (aka AI Agent As Another Unix User)

A barebone sandbox for agentic coding based on UNIX users.

Instead of containers or VMs, `auu` creates a dedicated UNIX user for your AI coding agent
and uses a setuid wrapper to run commands as that user.
This gives you filesystem-level isolation with minimal overhead — the agent runs on the
same host, but under a separate UID with controlled access.

## How it works

When you create an agent, `auu` will:

1. Create a UNIX user (e.g. `agent`) and a group (`su-as-agent`)
2. Add your user to the group so you can interact with the agent's files
3. Configure the agent's home directory with setgid + ACL defaults so files created by either user remain editable by both
4. Compile and install a small setuid C binary (`su_as_agent`) to execute command as the agent

The entrypoint binary is the key piece: it calls `setresuid`/`setresgid` to *permanently* become the agent user (no way back to the caller's UID).

## Requirements

- Linux with ACL support (`setfacl`/`getfacl`)
- A C compiler (for the setuid entrypoint)
- Python ≥ 3.12
- `sudo` access (for user/group creation and setuid setup)

## Installation

```bash
pipx install agent-as-unix-user
# or with uv
uv tool install agent-as-unix-user
```

This installs the `auu` command.

## Usage

### Create a new agent

```bash
auu new                    # creates an agent named "agent" (default)
auu new --agent agentA     # custom agent name
auu new --yes              # skip confirmation prompt
```

Requires root/sudo. Creates the UNIX user, group, home directory (with setgid + ACL), compiles the setuid entrypoint, and updates the config file.

### Run a command as the agent

```bash
auu run echo hello                    # run as default agent "agent"
auu run --agent agentA -- code        # run as a specific agent
auu run --env API_KEY=xxx -- cmd      # pass environment variables
```

Before executing, `auu run` verifies the entrypoint binary hasn't been modified by comparing its SHA-256 hash against the stored fingerprint. Environment is scrubbed by default — only `LANG` and `TERM` are kept, plus any variables passed explicitly via `--env`.

### Show agent info & health

```bash
auu info                   # info for default agent "agent"
auu info --agent agentA    # info for a specific agent
```

Displays the agent's home directory, group, entrypoint path, ACL external accesses, and runs a healthcheck that verifies:

- UNIX user and group exist
- Home directory exists with setgid bit
- Default ACLs are configured
- Entrypoint exists and is executable
- Current user is a member of the agent's group
- ACL tooling is available on the system

### List agents

```bash
auu list
```

Lists all agents present in the configuration file.

### Delete an agent

```bash
auu delete                        # delete default agent "agent"
auu delete --agent agentA         # delete a specific agent
auu delete --delete-home          # also remove the home directory
auu delete --yes                  # skip confirmation prompt
```

Requires root/sudo. Removes the UNIX user, group, and optionally the home directory. Resilient to partial state — if some resources are already gone, it skips them and continues.

### Global options

```
--config, -C PATH    Config file (default: ~/.config/agent-as-another-unix-user.toml)
--version            Show version
-h, --help           Show help
```

## Security model

- **UID isolation**: the agent runs as a separate UNIX user — it cannot read your home directory or other users' files (assuming standard permissions).
- **Permanent privilege drop**: the setuid entrypoint uses `setresuid`/`setresgid` to permanently become the agent user and .
- **Environment scrubbing**: `auu run` only pass `LANG` and `TERM` environ variables to the agent user (use `--env` to manually pass additional environ variables).
- **Shared filesystem via ACL**: the agent's home uses setgid + default ACLs on the group so both the human and the agent can read/write files without ownership conflicts.
