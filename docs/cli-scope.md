# What belongs in the command, and what belongs in the API

A decision, so that the next command is added on purpose rather than because
it was easy.

## The rule

**The `smoking-pi` command owns everything that has to work when the stack
does not. The API and the web admin own everything about what is measured.**

Everything follows from one fact: the API *is* a container in the stack it
would be managing. `config-manager` and `web-admin` come up with the rest of
Compose and go down with it. So "restart the stack from the web admin" is only
available exactly when you least need it, and an installer that lived in the
API would have to already be running in order to install itself.

That is not a criticism of the API. It is the reason the two surfaces are not
interchangeable, and the reason duplicating one into the other is not a
kindness.

## The three tiers

### Only the command

These need a shell, and most of them need the stack stopped. None of them can
be an HTTP endpoint without the endpoint becoming the thing that breaks.

| Command | Why it cannot live in the API |
| --- | --- |
| `install` | Nothing is running yet. It writes the env file the API reads. |
| `upgrade` | Recreates the containers, including the API's own. |
| `backup`, `restore` | Stop the stack for consistent volume tarballs. `restore` overwrites the env file. |
| `purge` | Deletes the volumes the API's database lives in. |
| `up`, `down` | `down` would kill the process serving the request. |
| `config` | Writes the env file and recreates the services that read the key, the API's own included. A settings page would have to restart the process serving it. |
| `passwords` | Reads the env file directly. Putting the secrets behind an HTTP endpoint would mean a second way to leak them. |
| `doctor` | Its static checks must run with the stack down; that is when they are wanted. |
| `paths`, `version` | Answer questions about the installation, not about the measurements. |

`logs` and `status` belong here for the same reason: when something is wrong,
the web admin is often the thing that is wrong.

### Only the API and the web admin

Targets, categories, sources, probes and schedules. These are rows in
PostgreSQL that the config-manager turns into SmokePing configuration, and
they have a UI built for them.

**There is deliberately no `smoking-pi add-target`.** It would be a second
writer to the same database with none of the validation the API does
(hostname checks, duplicate detection, category rules), and the regeneration
step afterwards is the API's job. The MCP server already exposes
`add_target`, `remove_target`, `toggle_target` and `apply_config` through the
API, which is the right shape: one writer, several front ends.

### Both, on purpose

`restart` and `status`, and only those.

- `smoking-pi restart` / `status` go through Compose. They work when the API
  is down, which is the case they exist for.
- The API's `POST /restart` and the web admin's restart button restart
  **SmokePing specifically**, right after a configuration change, from the
  screen where you made it. Sending someone to a terminal to apply a target
  they just added would be absurd.

Two implementations of two different things that share a word. The
duplication is in the vocabulary, not in the code.

## What this rules out

- No target, category or probe management in the command.
- No install, upgrade, backup, restore or purge in the API.
- No second copy of the doctor as an endpoint. If a check is useful from a
  browser, the doctor gains a machine-readable output and the API reads it;
  it does not gain a reimplementation.

## Before adding a command, answer these

1. **Does it need to work when the stack is down?** If yes, it is a command.
2. **Is it about the measurements rather than the installation?** If yes, it
   is an API endpoint with a UI, and the command should not learn it.
3. **Does something already own this state?** If the answer is PostgreSQL,
   the API owns it. A command that writes it is a second writer.
4. **If both surfaces need it, is it the same operation?** `restart` was not.
   Check before assuming the duplication is waste.

`smoking-pi openclaw` passes: it edits the env file, starts a service and
talks to a gateway on the host, none of which an in-stack API can do, and it
is about the installation rather than what is measured.

## One rule both surfaces follow: no guessing at container names

Because `restart` and `status` exist on both sides, both have to agree on
which container is which — and the answer is the Compose labels, never the
container's name. Backlog #7 settled that for the scripts; `config-manager`
followed in the same place this page was written.

The API asks two questions of the labels, and asks nothing else. *Is this
container ours?* is
`com.docker.compose.project` alone — a name test would have claimed an
unrelated `prometheus` or `proxy`, since the default project is `pro`. *Which
container is this service?* is the project label **and**
`com.docker.compose.service`, because a host running two editions has two
containers that answer to `smokeping`, and `POST /restart` must not reach the
other one. Compose labels every container it starts, including the ones that
set an explicit `container_name`, so the labels never miss something a name
pattern would have found. Where the answer is *none*, that is the answer:
the status check used to fall back to the name `<project>-smokeping-1`, and
now reports the container as absent rather than describing whichever
container happened to be wearing that name.
