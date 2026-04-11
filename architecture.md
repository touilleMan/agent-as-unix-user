## Goal

This project is a simple CLI that aims at configuring a light sandbox system for agentic coding using UNIX user.

## CLI usage

Common options:

- `--config CONFIG` (short: `-C CONFIG`): specify the config file, default to `$HOME/.config/agent-as-another-unix-user.toml`
- `--version`: display version and exit
- `--help` (short: `-h`): display help and exit

### Create a new agent

This will:

- create the configuration file if it doesn't exit
- update the configuratino file with the new agent
- create UNIX user and "su_as_agent_group" group
- create user home and configured it with segitd and ACL defaults to ensure
  its content can be edited by multiple users without ending with ownership
  and write access issues.
- copy the $HOME/README.md
- install the entrypoint binary

This command requires super-user capability.

```bash
au new # By default try to create an agent named `agent`
au new --user agentA
au new --yes # Don't ask for confirmation
```

### Delete an agent

This remove the configuration file and all the existing agent users, groups and home directory.
If some operations are not possible (e.g. the home directory doesn't exist), the script should
print an error and carry on (this is important as a typical usecase for this command it to remove
a half-broken agent).
This command requires super-user capability.

```bash
au delete  # By default try de delete agent named `agent`
au delete --user agentA
au delete --dry-run # Pretend to do the operation
au delete --yes # Don't ask for confirmation
```

### List existing agents

This will:

- List all agents present in the configuration file
- Do a healthcheck for each of them to ensure they work propertly.

Typically a healtcheck should ensure:

- the UNIX user, group, and home exist
- the entrypoint binary works correctly (and if it doesn't, the healtcheck should
  look if our user is member of the correct group and if the entrypoint script exist
  and is executable etc.).
- the agent home directory is correctly configured with segitd and ACL defaults to ensure
  its content can be edited by multiple users without ending with ownership and write access
  issues. Typically if that's not the case the healtcheck should check if the filesystem
  does support ACL.

The list should display useful information for each agent (home directory, healthcheck status).

```bash
au list
```

### Run a command as the agent

To run claude code in the agent:

```bash
au run code # By default use agent `agent`
au run --user agentA -- code
```

## Typical arborescence considering two agents (`agentA` and `agentB`)

Arborescence:

- /home/agentA/
- /home/agentA/README.md
- /home/agentA/su_as_agent
- /home/agentA/.config/agent-as-another-unix-user/su_as_agent-src/
- /home/agentB/.config/agent-as-another-unix-user/su_as_agent-src/main.c
- /home/agentB/.config/agent-as-another-unix-user/su_as_agent-src/Makefile
- /home/agentB/
- /home/agentB/README.md
- /home/agentB/su_as_agent
- /home/agentB/.config/agent-as-another-unix-user/su_as_agent-src/
- /home/agentB/.config/agent-as-another-unix-user/su_as_agent-src/main.c
- /home/agentB/.config/agent-as-another-unix-user/su_as_agent-src/Makefile

Note:

- `/home/agent-as-another-unix-user/README.md` should explain 
- `su_as_agent` file in the home is the entrypoint binary that allows to run commands as the agent user. This binary must
  be compiled for each agent since it contains the agent's UID hardcoded
- `README.md` in the agent home folder should explain:
  - what this directory is about
  - what configuration file it is related to (i.e. the `$HOME/.config/agent-as-another-unix-user.toml` of the user that created it)
  - how it can be correctly deleted (as removing the folder along leaves behind UNIX users and group)
  - what is `$HOME/su_as_agent`  and where it full source it located (i.e. `$HOME/.config/agent-as-another-unix-user/su_as_agent-src`)

## Typical configuration file

Config file `$HOME/.config/agent-as-another-unix-user.toml`

```toml
[[agents]]

# UNIX user
user_name = "agent-user"
# Group the current user must be part of to run the entrypoint
# `su_as_agent` binary.
# This name is stored here in order to run healthcheck and to
# know which group should be removed when destroying this agent.
su_as_agent_group = "su-as-agent-user"

# User home is not store here since `echo ~<user_name>` can be
# used instead.
# However it is expected to be `<base_dir>/<user_name>`.

# List of groups the agent is part of is not stored here since
# it can be queried instead using the `id <user_name>` command

# Command to execute in order to run commands as the agent user.
#
# This is typically a setuid wrapper (e.g. running
# `~/home/agent-user/su_as_agent echo hello` will first setuid
# to agent-user, then execute `echo hello`)
#
# Note that, for it to work, your current user must be part of
# the `su-as-<user_name>` group, this should have been configured
# automatically when this agent user has been created.
entrypoint = "$HOME/su_as_agent"
```

## The entrypoint binary

The entrypoint is a  setuid wrapper script (classic UNIX trick).

In practice it is a small C program owned by the agent user with the setuid bit:

```C
// main.c
#include <unistd.h>
int main() {
    setuid(TARGET_UID);  // agent's uid
    char *args[] = {"/bin/bash", "-l", NULL};
    execv("/bin/bash", args);
}
```

When a new agent is setup, the source code is copied in the new agent home along with a Makefile (
in `$HOME/.config/agent-as-another-unix-user/su_as_agent-src`), the Makefile is then run as
the agent user to compile the program an set its correct usermod/group.

## The home folder configuration

The agent user's home folder is expected to contain data that will be also edited by the human user
(typically a project git repo will be cloned in the agent user's home, then both agentic code and
human coding will occur there).

This is done by having the agent user's home folder being shared with the "su_as_agent_group" UNIX group
(i.e. only the human user that have create the agent is member of this groupe).

Both the setgid bit should be set in the directory and also ACL defaults to ensure the "su_as_agent_group"
always get write read+access no matter who created the file.
