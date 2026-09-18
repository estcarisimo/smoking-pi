# Security Policy

## Reporting a vulnerability

**Please report privately, not in a public issue.**

Use GitHub's private vulnerability reporting:
[**Report a vulnerability**](https://github.com/estcarisimo/smoking-pi/security/advisories/new).
It opens a draft advisory visible only to you and the maintainers, so a fix
can be prepared before anything is public. No email address is needed and
nothing is disclosed by filing it.

If that link 404s, private reporting is not enabled on the repository — open a
public issue saying only *"security issue, please enable private reporting"*
with no details, and wait.

This is a self-hosted hobby project maintained by one person (the owner in
[CODEOWNERS](CODEOWNERS) is who reads the reports). There is no
response-time commitment; expect an acknowledgement within a week and a fix
on a timescale that depends on severity and on how much of the stack it
touches.

### What to include

Enough to reproduce it without guessing:

- Edition (Basic / Standard / Pro), the version or commit, and the
  time-series backend (`TSDB_TYPE`).
- Which service (web-admin, config-manager, mcp-server, alerter, an
  exporter, a compose file, a script) and the entry point — URL, MCP tool,
  env var, config value.
- Steps or a request that demonstrates it, and what you observed. A
  minimal target name, YAML snippet or request body is worth more than a
  scanner report.
- What an attacker gains: which credential, which data, which host.
- Anything you already know about the fix, if you have looked.

Redact your own secrets before pasting logs; the generated `.env` and the
config-manager `/status` output are the usual places they hide.

### How disclosure works

1. You file the advisory; the maintainer acknowledges it in the advisory
   thread.
2. The fix is developed on a private fork of the advisory (GitHub's
   temporary private fork), not on a public branch. CI does not run there,
   so the fix is tested on the reference Pi.
3. A release ships the fix; the advisory is published the same day with
   affected versions, the CVSS estimate, and credit to the reporter (unless
   you ask to stay anonymous). The CHANGELOG names the advisory.
4. If a fix is not possible or the report is out of scope, the advisory says
   so and is closed; you are free to publish after that.

If you have heard nothing after 14 days, comment on the advisory; if still
nothing after 30, you are free to disclose. Please say you intend to.

## Scope

This project deploys a monitoring stack onto a machine you control. The
interesting attack surface is mostly **configuration**, so these count:

- Credentials, tokens or passwords written somewhere they can be committed,
  logged, or served — `.env` handling, generated config files, container
  logs, API responses.
- Anything reachable without authentication that should not be: an exposed
  service port, a Cloudflare tunnel fronting a service that assumes it is on
  a LAN, a Grafana snapshot embedding data.
- Injection through values an operator can set — target names reach SQL,
  Jinja templates, generated SmokePing config, shell, and Telegram HTML.
- Privilege issues in the containers: unnecessary root, host mounts,
  `network_mode: host` consequences.
- Dependency vulnerabilities that are actually reachable from how this code
  uses the dependency.

Out of scope: anything requiring an attacker who already has root on the
host, findings in SmokePing / Grafana / InfluxDB / ClickHouse themselves
(report those upstream), and results from a scanner with no demonstrated
impact here.

## What this project assumes about your deployment

Worth stating, because several are load-bearing and getting one wrong is more
likely to hurt you than a bug in this code:

- **Services expect a trusted network.** Grafana, web-admin and the
  config-manager API are not hardened for the open internet. If you put a
  tunnel in front of one, put authentication in front of the tunnel.
- **`.env` files hold real secrets** and are gitignored for that reason.
  `setup.sh` generates strong values; do not commit them, and rotate anything
  that reaches a terminal, a screenshot, or a chat log.
- **The MCP server binds `127.0.0.1` by default** and its bearer token is
  what protects it from other processes on the same host. An empty
  `MCP_API_TOKEN` means unauthenticated.
- **Alert delivery sends network data to a third party** (a Telegram chat, a
  webhook). Latency, loss and target names all leave the machine.

## Supported versions

The latest release and `main`. Older tags are not patched.
