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

## Known gaps, recorded here rather than left implicit

Backlog #7 established that nothing should guess a container name, and fixed
every script. Two places inside the stack still guess:

- `config-manager`'s `list_containers` treats a container as part of the
  project when the project name is a **substring** of the container name
  (`api.py:1205`). With the project called `pro`, an unrelated `prometheus`
  or `proxy` container on the same host is reported as part of the stack.
- `resolve_container_name` matches on the `com.docker.compose.service` label
  **without also matching the project** (`api.py:1145`). On a host running two
  editions, the first match wins, and a restart could reach the other one.

Neither misfires on the reference Pi today — its only labeled containers are
the `pro` project's, and the three `tunnel-*` containers match neither test.
Both are one-line fixes in code this page does not otherwise touch, so they
are written down here rather than folded into a document.
