# shellcheck shell=bash
# smoking-pi fleet: this installation's place in a Fleet (docs/fleet.md).
# Sourced by cli/smoking-pi, never run on its own.
#
# This release has one subcommand, `status`. Creating a fleet, joining and
# leaving one, and the lead's commands come with the lead and the agent
# that implement them (docs/fleet.md, "What is not built yet").

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
    enrolled = bool(agent.get("enrolled"))
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
    print(f"  agent:      {agent_state}")
print("Joining a fleet is not built yet: this release gives the installation its identity.")
PY
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
        ""|-h|--help) command_help fleet ;;
        *) echo "unknown fleet command: $sub (this release has: status)" >&2; return 2 ;;
    esac
}
