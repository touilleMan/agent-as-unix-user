# A⁴U²: AI Agent As Another Unix User (aka agent-as-unix-user)

A bare-bones sandbox for agentic coding based on UNIX users.

TL;DR:

```shell
uv tool install agent-as-unix-user  # Install this CLI
au new  # Create a new UNIX user for your AI agent
au run --env FOO=bar bash  # Run bash (with an environment variable) as the agent UNIX user,
                           # with only access to `/home/agent`
au mount add --rw ~/example # Expose `~/example` in `/home/agent/example`
                            # with read&write access
cd ~/example && au run bash # `au` detects current directory corresponds to agent's
                            # `/home/agent/example`, so it changes to it before starting bash
au run -- claude --dangerously-skip-permissions # Run Claude in yolo mode without guilt,
                                                # the UNIX sandbox has you covered \o/
```

UNIX has been designed from the ground up to allow multiple users to securely share a single machine
(remember the time when a terminal was a physical thing that multiple users used to connect to a single mainframe? Yeah, me neither).

More recently, Android uses a similar technique to isolate each application by having a dedicated user for each application.

So why not do the same for agentic coding? Enter `agent-as-unix-user`, a simple wrapper around standard UNIX commands to easily:

- Create a dedicated UNIX user
- Give access to certain folders in read-only or read&write mode
- Undo all of this if needed ;-)
- Run commands as the UNIX user

Of course, this solution has tradeoffs, but it offers a surprisingly high bang for the buck:

Pros:

- Unlike containers and VMs, there's no need to eat gigabytes of disk or deal with manual start/stop.
- Strong isolation: you can run your agent in full yolo mode knowing it can only break its own home directory.
- Simple to understand (so simple that [all actual commands are printed before being executed](#transparency-as-a-feature)!)
- Simple to reason about: the agent can only modify its home and cannot read your home. From there you can easily
  give it access (in read-only or read-write) to some specific folders in your home.
- Can be extended using the regular UNIX ecosystem (see the [recipes below](#recipes)).

Cons:

- Requires root access to create the agent user (this is done once; after that root is not needed to run a command as the agent)
- No network filtering out-of-the-box (but can [be easily added using an HTTP proxy](#example-2-filter-network-traffic)).
- Tricky to share the commands you installed in your home (i.e. basically everything you installed with `curl https://someapp.com/install.sh | bash`).
  Note this is a user experience con (as it means reinstalling your tools for the agent user), but also a security pro since otherwise a malicious
  agent could escape the sandbox by just modifying a shared tool and waiting for you to run it...

## Installation

Only Linux is supported for now (macOS might be possible though; open an issue if you are interested \o/).

```bash
pipx install agent-as-unix-user
# or with uv
uv tool install agent-as-unix-user
```

> [!NOTE]
> agent-as-unix-user has the following requirements:
>
> - Linux with ACL support
> - A C compiler (for the setuid entrypoint, see [below](#how-it-works))
> - `sudo` access (for user/group creation and setuid setup)

## Recipes

The great thing about this approach is its simplicity and its compatibility with the UNIX ecosystem.

### Example 1: limit CPU & RAM

Limit the agent to use at most 16GB of RAM and one and a half cores on your machine.

```shell
systemctl set-property user-$(id -u agent).slice CPUQuota=150% MemoryMax=16G
```

### Example 2: filter network traffic

Run an HTTP proxy locally with your filtering rules, then configure iptables:

```shell
# Redirect HTTP and HTTPS traffic from UNIX user `agent` to
# port 3128 (default port for Squid HTTP proxy)
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
4. Compile and install a small setuid C binary (`/home/agent/su_as_agent`) to execute commands as the agent.

To run a command as an agent, `au run my_command` itself uses `/home/agent/su_as_agent`, which:

- Scrubs your environment variables (only `LANG` and `TERM` are kept).
- Drops all the groups inherited from the original user.
- Sets UID and GID to become the agent user.

## Transparency as a feature

Agent-as-unix-user is designed to be as transparent as possible by only using UNIX commands and displaying them as they are run.

For instance, running `$ au new -a agent2` will result in the following output:

```bash
Create agent agent2 in /home/agent2 and configure group 'su-as-agent2'? [y/N]: y
$ sudo groupadd su-as-agent2
[sudo] password for touilleMan:
$ sudo useradd --shell /usr/bin/bash --no-user-group --create-home --home-dir /home/agent2 --gid su-as-agent2 agent2
$ sudo usermod --append --groups su-as-agent2 touilleMan
$ sudo chgrp su-as-agent2 /home/agent2
$ sudo chmod 2770 /home/agent2
$ sudo setfacl --modify default:group:su-as-agent2:rwx /home/agent2
$ sg su-as-agent2 -c 'tee /home/agent2/README.md'
$ sg su-as-agent2 -c 'mkdir -p /home/agent2/.config/agent-as-unix-user/su_as_agent-src'
$ sg su-as-agent2 -c 'tee /home/agent2/.config/agent-as-unix-user/su_as_agent-src/main.c'
$ sg su-as-agent2 -c 'tee /home/agent2/.config/agent-as-unix-user/su_as_agent-src/Makefile'
$ sg su-as-agent2 -c 'make -C /home/agent2/.config/agent-as-unix-user/su_as_agent-src'
make: Entering directory '/home/agent2/.config/agent-as-unix-user/su_as_agent-src'
cc -O2 -Wall -Wextra -Werror -DTARGET_UID=1003 -DTARGET_GID=1003 -o su_as_agent main.c
make: Leaving directory '/home/agent2/.config/agent-as-unix-user/su_as_agent-src'
$ sg su-as-agent2 -c 'mv --force /home/agent2/.config/agent-as-unix-user/su_as_agent-src/su_as_agent /home/agent2/su_as_agent'
$ sudo chown root:su-as-agent2 /home/agent2/su_as_agent
$ sudo chmod 4750 /home/agent2/su_as_agent
Created agent agent2
```

> [!NOTE]
> Here, lines starting with `$` don't indicate user input but instead display the commands that `au` is about to execute.

## Usage

### Create a new agent

```bash
au new                    # creates an agent named "agent" (default)
au new --agent agentA     # custom agent name
au new --yes              # skip confirmation prompt
```

Requires root/sudo. Creates the UNIX user, group, home directory (with setgid + ACL), compiles the setuid entrypoint, and updates the config file.

### Run a command as the agent

```bash
au run echo hello                    # run as default agent "agent"
au run --agent agentA -- code        # run as a specific agent
au run --env API_KEY=xxx -- cmd      # pass environment variables
```

Before executing, `au run` verifies the entrypoint binary hasn't been modified by comparing its SHA-256 hash with the stored fingerprint.
The environment is scrubbed by default — only `LANG` and `TERM` are kept, plus any variables passed explicitly via `--env`.

### Show agent info & health

```bash
au info                   # info for default agent "agent"
au info --agent agentA    # info for a specific agent
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
au list
```

Lists all agents present in the configuration file.

### Delete an agent

```bash
au delete                        # delete default agent "agent"
au delete --agent agentA         # delete a specific agent
au delete --delete-home          # also remove the home directory
au delete --yes                  # skip confirmation prompt
```

Requires root/sudo. Removes the UNIX user, group, and optionally the home directory.
Resilient to partial state — if some resources are already gone, it skips them and continues.

### Global options

```
--config, -C PATH    Config file (default: ~/.config/agent-as-unix-user.toml)
--version            Show version
-h, --help           Show help
```
