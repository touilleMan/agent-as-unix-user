# A⁴U², aka AI Agent As Another Unix User, aka just agent-as-unix-user

A barebone sandbox for agentic coding based on UNIX users.

TL;DR:

```shell
uv tool install agent-as-unix-user  # Install this cli
auu new  # Create a new UNIX user for your IA agent
auu mount add --rw ~/example # Expose `~/example` in `/home/agent/example` with read&write access
auu run --env FOO=bar claude  # Run claude (with an environ variable) as the agent UNIX user, with only access to `/home/agent`
```

UNIX has been designed from the ground to allow multiple users to securely share a single machine (remember
the time when a terminal was a physical thing that was used to connect to a mainframe ? Yeah, me neither).

So instead of containers or VMs, agent-as-unix-user is a simple wrapper around standard UNIX commands to easily:

- Creates a dedicated UNIX user
- Give access to certain folders in read-only or read&write mode
- Undo all of this if needed ;-)
- Run commands as the UNIX user

With your IA agent running as a dedicate user, protection becomes trivial:

This gives you filesystem-level isolation with minimal overhead: the agent runs on the
same host, but under a separate UID with controlled access.

## Installation

Only Linux is supported for now (MacOS might be possible though, so PR welcome \o/).

```bash
pipx install agent-as-unix-user
# or with uv
uv tool install agent-as-unix-user
```

> [!NOTE]
> agent-as-unix-user has the following requirements:
>
> - Linux with ACL support
> - A C compiler (for the setuid entrypoint)
> - `sudo` access (for user/group creation and setuid setup)

## Recipes

The great thing about this approach is its simplicity and it compatibility with the UNIX ecosystem.

## Example 1: limit CPU & RAM

Limit the agent to use at most 16Go of RAM and one-and-half cores on your machine.

```shell
systemctl set-property user-$(id -u agent).slice CPUQuota=150% MemoryMax=16G
```

## Example 2: filter network traffic

Run an HTTP proxy locally with your filtering rules, then configuring iptables:

```shell
# Redirect HTTP and HTTPS traffic from UNIX user `agent` to port 3128 (default port for Squid HTTP proxy)
iptables -t nat -A OUTPUT -m owner --uid-owner agent -p tcp --dport 80  -j REDIRECT --to-port 3128
iptables -t nat -A OUTPUT -m owner --uid-owner agent -p tcp --dport 443 -j REDIRECT --to-port 3128

# Allow DNS
iptables -A OUTPUT -m owner --uid-owner agent -p udp --dport 53 -j ACCEPT

# Block everything else for that user
iptables -A OUTPUT -m owner --uid-owner agent -j DROP
```

## How it works

When you create an agent, agent-as-unix-user will:

1. Create a UNIX user (e.g. `agent`) and a group (`su-as-agent`)
2. Add your user to the group so you can interact with the agent's files
3. Configure the agent's home directory with setgid + ACL defaults so files created by either user remain editable by both
4. Compile and install a small setuid C binary (`/home/agent/su_as_agent`) to execute command as the agent.

Note agent-as-unix-user is designed to be as transparent as possible by only using UNIX commands and displaying them as they are run:

```bash
$ uv run auu new -a agent2
Create agent agent2 in /home/agent2 and configure group 'su-as-agent2'? [y/N]: y
$ sudo groupadd su-as-agent2
[sudo] password for touilleMan:
$ sudo useradd --shell /usr/bin/bash --no-user-group --create-home --home-dir /home/agent2 --gid su-as-agent2 agent2
$ sudo usermod --append --groups su-as-agent2 touilleMan
$ sudo chgrp su-as-agent2 /home/agent2
$ sudo chmod 2770 /home/agent2
$ sudo setfacl --modify default:group:su-as-agent2:rwx /home/agent2
$ sg su-as-agent2 -c 'tee /home/agent2/README.md'
$ sg su-as-agent2 -c 'mkdir -p /home/agent2/.config/agent-as-another-unix-user/su_as_agent-src'
$ sg su-as-agent2 -c 'tee /home/agent2/.config/agent-as-another-unix-user/su_as_agent-src/main.c'
$ sg su-as-agent2 -c 'tee /home/agent2/.config/agent-as-another-unix-user/su_as_agent-src/Makefile'
$ sg su-as-agent2 -c 'make -C /home/agent2/.config/agent-as-another-unix-user/su_as_agent-src'
make: Entering directory '/home/agent2/.config/agent-as-another-unix-user/su_as_agent-src'
cc -O2 -Wall -Wextra -Werror -DTARGET_UID=1003 -DTARGET_GID=1003 -o su_as_agent main.c
make: Leaving directory '/home/agent2/.config/agent-as-another-unix-user/su_as_agent-src'
$ sg su-as-agent2 -c 'mv --force /home/agent2/.config/agent-as-another-unix-user/su_as_agent-src/su_as_agent /home/agent2/su_as_agent'
$ sudo chown root:su-as-agent2 /home/agent2/su_as_agent
$ sudo chmod 4750 /home/agent2/su_as_agent
Created agent agent2
```

To run a command as an agent, `auu run my_command` will itself uses `/home/agent/su_as_agent` that:

- Scrubs your environ variables (only `LANG` and `TERM` are kept).
- Drops all the groups inherited from the original user.
- Sets UID and GID to become the agent user.

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
