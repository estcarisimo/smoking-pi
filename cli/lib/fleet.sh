# shellcheck shell=bash
# smoking-pi fleet: this installation's place in a Fleet (docs/fleet.md).
# Sourced by cli/smoking-pi, never run on its own.
#
# Subcommands: `status`, and on a node `join` and `leave` (fleet_join,
# fleet_leave below). The lead's commands come with the lead
# (docs/fleet.md, "What is not built yet").

# A version 4 UUID, lowercase. /proc has one on Linux; elsewhere (macOS)
# 16 random bytes with the version and variant bits set.
fleet_new_uuid() {
    if [ -r /proc/sys/kernel/random/uuid ]; then
        tr 'A-F' 'a-f' < /proc/sys/kernel/random/uuid
        return
    fi
    local h
    h="$(od -An -tx1 -N16 /dev/urandom | tr -d ' \n')"
    printf '%s-%s-4%s-%x%s-%s\n' "${h:0:8}" "${h:8:4}" "${h:13:3}" \
        "$(( (0x${h:16:1} & 3) | 8 ))" "${h:17:3}" "${h:20:12}"
}

fleet_valid_id() {
    # Whole string (bash's `$` is end of string, not before a newline), and
    # the C locale so the ranges mean ASCII.
    local LC_ALL=C
    [[ "$1" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ ]]
}

# This installation's node id: created the first time Fleet is touched
# (`fleet status`, `enable fleet`, `enable fleet-lead`) and kept in the env
# file from then on, so upgrades keep it and `backup`/`restore` carry it.
# Never regenerated: an id that is there but malformed was edited by hand,
# and replacing it would silently make this a different node.
fleet_ensure_node_id() {
    local id
    id="$(env_get FLEET_NODE_ID)"
    if [ -n "$id" ]; then
        fleet_valid_id "$id" && return 0
        echo "FLEET_NODE_ID in $ENV_FILE is not a node id (a lowercase version 4 UUID)." >&2
        echo "It is created once and never edited; see docs/fleet.md, \"Two installations, one node id\"." >&2
        return 1
    fi
    id="$(fleet_new_uuid)"
    fleet_valid_id "$id" || { echo "could not create a node id" >&2; return 1; }
    env_set FLEET_NODE_ID "$id"
    echo "Created this installation's Fleet node id: $id (kept in the env file; 'smoking-pi backup' carries it)."
}

# node, lead, node+lead or none, from the profiles that are on.
fleet_role() {
    local agent=0 lead=0
    profile_on fleet && agent=1
    profile_on fleet-lead && lead=1
    case "$agent$lead" in
        10) echo node ;;
        01) echo lead ;;
        11) echo node+lead ;;
        *) echo none ;;
    esac
}

# The agent's own status (fleet/status.py), as JSON; empty when it is not
# running. Read-only: `exec` into the running container, nothing started.
fleet_agent_status() {
    profile_on fleet || return 0
    compose exec -T fleet-agent python -m fleet.status 2>/dev/null || true
}

fleet_status() {
    local json=0
    while [ $# -gt 0 ]; do
        case "$1" in
            --json) json=1; shift ;;
            -h|--help) command_help fleet; return 0 ;;
            *) echo "unknown option $1" >&2; return 2 ;;
        esac
    done
    fleet_ensure_node_id >&2 || return 1
    local id name site urls mode role agent_json
    id="$(env_get FLEET_NODE_ID)"
    name="$(env_get FLEET_NODE_NAME)"; name="${name:-node-${id:0:8}}"
    site="$(env_get FLEET_SITE_NAME)"; site="${site:-default}"
    urls="$(env_get FLEET_LEAD_URLS)"
    mode="$(env_get FLEET_LEAD_MODE)"; mode="${mode:-node}"
    role="$(fleet_role)"
    agent_json="$(fleet_agent_status)"
    # One parser for both outputs: the agent's JSON is read, never
    # interpolated, and every value leaves through json.dumps or print.
    FLEET_ID="$id" FLEET_NAME="$name" FLEET_SITE="$site" FLEET_URLS="$urls" \
    FLEET_MODE="$mode" FLEET_ROLE="$role" FLEET_AGENT="$agent_json" FLEET_JSON="$json" \
    FLEET_AGENT_ON="$(profile_on fleet && echo 1 || echo 0)" FLEET_CLI="$(cli_name)" \
    python3 - <<'PY'
import json, os

e = os.environ
urls = [u.strip() for u in e["FLEET_URLS"].split(",") if u.strip()]
role = e["FLEET_ROLE"]
agent_on = e["FLEET_AGENT_ON"] == "1"
try:
    agent = json.loads(e["FLEET_AGENT"]) if e["FLEET_AGENT"].strip() else {}
except ValueError:
    agent = {}
if not agent_on:
    agent_state, enrolled = "off", None
elif not agent.get("state"):
    agent_state, enrolled = "not_running", None
else:
    agent_state = str(agent["state"])
    # A corrupt or mismatched enrollment is not "no": it waits for a person.
    enrolled = (None if agent_state in ("state_corrupt", "identity_mismatch")
                else bool(agent.get("enrolled")))
# The agent's own report of its link to the lead (fleet/status.py FIELDS):
# read, never interpolated into a command.
link = {k: agent.get(k) for k in ("lead_link", "lead_in_use", "last_heartbeat",
                                  "next_heartbeat", "failures", "last_error",
                                  "cert_not_after", "renew_after", "revoked_at",
                                  "detail", "site_id", "store_received")}
agent_urls = agent.get("lead_urls") if isinstance(agent.get("lead_urls"), list) else None
out = {
    "node_id": e["FLEET_ID"],
    "node_name": e["FLEET_NAME"],
    "site_name": e["FLEET_SITE"],
    "role": role,
    "lead_mode": e["FLEET_MODE"] if "lead" in role else None,
    "lead_urls": urls,
    "agent": agent_state,
    "enrolled": enrolled,
    "fleet_id": agent.get("fleet_id") if enrolled else None,
    "site_id": link["site_id"] if enrolled else None,
    # The list the agent holds (a lead may have replaced the one joined with).
    "agent_lead_urls": agent_urls if enrolled else None,
    # The link to the lead (ok, failing, idle); the container's own health
    # is only whether the agent process is alive.
    "lead_link": link["lead_link"],
    "lead_in_use": link["lead_in_use"],
    "last_heartbeat": link["last_heartbeat"],
    "next_heartbeat": link["next_heartbeat"],
    "failures": link["failures"],
    "last_error": link["last_error"],
    "cert_not_after": link["cert_not_after"],
    "renew_after": link["renew_after"],
    "revoked_at": link["revoked_at"],
    "store_received": link["store_received"],
}
if e["FLEET_JSON"] == "1":
    print(json.dumps(out, indent=1))
    raise SystemExit(0)

cli = e["FLEET_CLI"]
roles = {
    "none": f"none (Fleet is off: {cli} enable fleet)",
    "node": "node (the fleet agent is on)",
    "lead": "lead",
    "node+lead": "node and lead",
}
if "lead" in role:
    mode = out["lead_mode"]
    roles[role] += f", lead mode {mode}"
    if mode == "node" and role == "lead":
        roles[role] += f" (this host measures too: {cli} enable fleet)"
if agent_state == "off":
    enrolled_text = "not known (the fleet agent is off)"
elif agent_state == "not_running":
    enrolled_text = f"not known (the fleet agent is not running: {cli} up)"
elif agent_state == "state_corrupt":
    enrolled_text = "not known: the agent's enrollment file is corrupt (docs/fleet.md)"
elif agent_state == "identity_mismatch":
    enrolled_text = ("not as this node: the agent is enrolled under another node id "
                     "(docs/fleet.md, \"Two installations, one node id\")")
elif agent_state == "revoked":
    enrolled_text = (f"revoked from fleet {out['fleet_id'] or '?'} at {link['revoked_at'] or '?'}: "
                     f"nothing is sent; '{cli} fleet leave' clears it")
elif agent_state == "cert_expired":
    enrolled_text = (f"yes, fleet {out['fleet_id'] or '?'}, but the certificate expired "
                     f"({link['cert_not_after'] or '?'}): re-join with '{cli} fleet leave "
                     f"--force' then '{cli} fleet join'")
elif enrolled:
    enrolled_text = f"yes, fleet {out['fleet_id'] or '?'}"
else:
    enrolled_text = "no"
print("Fleet (docs/fleet.md)")
print(f"  node id:    {out['node_id']}")
print(f"  node name:  {out['node_name']}")
print(f"  site:       {out['site_name']}")
print(f"  role:       {roles[role]}")
print(f"  leads:      {', '.join(urls) if urls else 'none set'}")
print(f"  enrolled:   {enrolled_text}")
if agent_state not in ("off", "not_running"):
    print(f"  agent:      {agent_state}"
          + (f" (link to the lead: {link['lead_link']})" if link["lead_link"] else ""))
if enrolled:
    if agent_urls and agent_urls != urls:
        print(f"  lead list:  {', '.join(agent_urls)} (the lead's latest; the agent uses it)")
    if agent_state == "stopped" and link["detail"]:
        print(f"  stopped:    {link['detail']}")
    print(f"  lead now:   {link['lead_in_use'] or 'none answered yet'}")
    print(f"  heartbeat:  last {link['last_heartbeat'] or 'never (since the agent started)'}"
          + (f", next {link['next_heartbeat']}" if link["next_heartbeat"] else ""))
    if link["failures"]:
        print(f"  failing:    {link['failures']} attempt(s): {link['last_error'] or '?'}"
              " (the node keeps measuring on its own)")
    if link["cert_not_after"]:
        print(f"  cert until: {link['cert_not_after']} (renews from {link['renew_after']})")
elif agent_state in ("off", "not_running", "not_enrolled"):
    print(f"Join a fleet: {cli} fleet join (paste the join string '{cli} fleet token' "
          "printed on the lead).")
PY
}

# --- join and leave (the node's side; docs/fleet.md, "Joining a fleet") ------

# The enrollment runs in the fleet image, not on the host: that is where the
# key is generated and kept (the agent's volume, as the agent's user) and
# where `cryptography` is. `compose run` of the agent's service gets the join
# string on its stdin: the string carries a single-use token, so it never
# goes in an argument (every user on the host sees arguments in `ps`, and
# the shell keeps them in its history), the environment, the env file or a
# log. fleet/join.py says the rest.
fleet_run() {
    # fleet_run MODULE [ARGS...]: python -m fleet.MODULE in a one-off agent
    # container, stdin passed through; its stdout is the result (JSON).
    compose run --rm --no-deps -T fleet-agent python -m "fleet.$1" "${@:2}"
}

fleet_join() {
    local js="" out rc=0
    while [ $# -gt 0 ]; do
        case "$1" in
            -h|--help) command_help fleet; return 0 ;;
            spfleet*)
                echo "Not as an argument: the join string holds a token, and arguments show in 'ps' and the shell's history." >&2
                echo "Run '$(cli_name) fleet join' and paste it when asked, or pipe it in: $(cli_name) fleet join < file" >&2
                return 2 ;;
            *) echo "unknown option $1" >&2; return 2 ;;
        esac
    done
    fleet_ensure_node_id >&2 || return 1
    if [ -t 0 ]; then
        printf 'Paste the join string (from %s fleet token on the lead; it is not shown): ' "$(cli_name)" >&2
        IFS= read -rs js || true
        echo >&2
    else
        IFS= read -r js || true
    fi
    js="${js//[[:space:]]/}"
    case "$js" in
        spfleet1_*) ;;
        "") echo "No join string given." >&2; return 2 ;;
        *) echo "That is not a join string (it starts with spfleet1_). Copy it again from '$(cli_name) fleet token' on the lead." >&2; return 2 ;;
    esac
    echo "Joining: the lead's root is checked against the join string, then this node enrolls (its key is made here and never leaves it)."
    out="$(printf '%s\n' "$js" | fleet_run join)" || rc=$?
    js=""
    if [ "$rc" -ne 0 ]; then
        [[ "$out" == *'"joined"'* ]] || echo "The join could not run (see above). Nothing was changed." >&2
        return 1
    fi
    local urls summary
    if ! urls="$(FLEET_OUT="$out" python3 -c 'import json, os; print(",".join(json.loads(os.environ["FLEET_OUT"])["lead_urls"]))' 2>/dev/null)" || [ -z "$urls" ]; then
        echo "The join answered something unexpected; '$(cli_name) fleet status' shows where the node stands." >&2
        return 1
    fi
    summary="$(FLEET_OUT="$out" python3 -c '
import json, os
d = json.loads(os.environ["FLEET_OUT"])
fleet, node, lead, until = d["fleet_id"], d["node_id"], d["lead_in_use"], d["not_after"]
print(f"Joined fleet {fleet} as node {node}, through {lead}.")
print(f"Its certificate is valid until {until} and renews by itself.")
if d.get("store_received"):
    print("The lead named a store to replicate to; this release keeps it but does not replicate yet.")
')"
    echo "$summary"
    env_set FLEET_LEAD_URLS "$urls"
    echo "FLEET_LEAD_URLS=$urls"
    if profile_on fleet; then
        config_apply FLEET_LEAD_URLS
    else
        cmd_services enable fleet
    fi
    echo "The node keeps measuring as before; '$(cli_name) fleet status' shows its heartbeats."
}

fleet_leave() {
    local force=() out rc=0 was_on=0
    while [ $# -gt 0 ]; do
        case "$1" in
            --force) force=(--force); shift ;;
            -h|--help) command_help fleet; return 0 ;;
            *) echo "unknown option $1" >&2; return 2 ;;
        esac
    done
    # The agent and the leave never write the enrollment at the same time.
    if profile_on fleet; then
        was_on=1
        compose stop fleet-agent >/dev/null 2>&1 || true
    fi
    out="$(fleet_run leave "${force[@]}" </dev/null)" || rc=$?
    if [ "$rc" -ne 0 ]; then
        # Still enrolled: the agent goes back to work.
        [ "$was_on" = 0 ] || compose up -d fleet-agent >/dev/null 2>&1 || true
        [[ "$out" == *'"left"'* ]] || echo "Leaving could not run (see above). Nothing was changed." >&2
        return 1
    fi
    # The lead list was this node's; on a host that is also the lead, it is
    # the lead's own setting too, and stays.
    profile_on fleet-lead || env_set FLEET_LEAD_URLS ""
    [ "$was_on" = 0 ] || cmd_services disable fleet
    echo "This installation is standalone again. Its measurements and history are untouched."
}

cmd_fleet() {
    need_edition
    if ! edition_has_profile fleet; then
        echo "Fleet is part of the Pro edition; this is $SMOKING_PI_EDITION." >&2
        return 2
    fi
    local sub="${1:-}"; shift || true
    case "$sub" in
        status) fleet_status "$@" ;;
        join) fleet_join "$@" ;;
        leave) fleet_leave "$@" ;;
        ""|-h|--help) command_help fleet ;;
        *) echo "unknown fleet command: $sub (this release has: status, join, leave)" >&2; return 2 ;;
    esac
}
