# shellcheck shell=bash
# smoking-pi core: the stack itself: Compose, its lock, volumes, profiles, the doctor,
# the address this host answers on.
# Sourced by cli/smoking-pi, never run on its own.

env_locked() {
    # The env file is there, but this user cannot read it. A packaged
    # install keeps it in /etc/smoking-pi, root's and 0750 because it holds
    # the passwords, so without sudo the file cannot even be seen: `-f` and
    # `-e` are false, and every reader here took that for "not installed"
    # (the bare command said to run install; config said every key was
    # unset; dns status said the observer was down). The edition file sits
    # beside it, so the edition is unknown too.
    [ "$(id -u)" != 0 ] || return 1
    [ -r "$ENV_FILE" ] && return 1
    [ -e "$ENV_FILE" ] && return 0
    local dir
    dir="$(dirname "$ENV_FILE")"
    [ -d "$dir" ] && [ ! -x "$dir" ]
}

need_edition() {
    [ -d "$EDITION_DIR" ] || { echo "no edition at $EDITION_DIR" >&2; exit 1; }
}

profiles() {
    # COMPOSE_PROFILES is written into the env file by setup.sh; honor it.
    [ -f "$ENV_FILE" ] && sed -n 's/^COMPOSE_PROFILES=//p' "$ENV_FILE" | tail -1 || true
}

STACK_LOCKED=0

stack_lock() {
    # One command at a time may change the containers. Two `compose up`s on
    # one project race each other's recreates: Compose replaces a container
    # by creating "<old id>_<name>", stopping and removing the old one, then
    # renaming -- two runs that saw the same old container pick the same
    # temporary name, and the second is refused ("Conflict. The container
    # name "/<id>_pro-influxdb-1" is already in use"), or finds the old one
    # already being removed. Both v2.13.2 and v2.13.7 upgrades on the
    # reference Pi hit it: a second session's `upgrade` or `config set` was
    # running at the same time. Held on the edition directory (readable by
    # the root of a package install and the user of a clone alike, nothing
    # to create or leave behind) until this command exits. No flock (macOS):
    # no lock, as before.
    [ "$STACK_LOCKED" = 1 ] && return 0
    command -v flock >/dev/null 2>&1 || return 0
    exec 9<"$EDITION_DIR"
    if ! flock -n 9; then
        echo "Another smoking-pi command is changing this stack; waiting for it to finish." >&2
        flock 9
    fi
    STACK_LOCKED=1
}

compose() {
    need_edition
    case "${1:-}" in
        up|down|create|start|stop|restart|rm|kill) stack_lock ;;
    esac
    if [ "${1:-}" = up ]; then shift; compose_up "$@"; return; fi
    compose_exec "$@"
}

compose_exec() {
    local p; p="$(profiles)"
    local extra=(--env-file "$ENV_FILE" -f docker-compose.yml)
    if [ "$SMOKING_PI_EDITION" = pro ] && [[ "$p" == *clickhouse* ]]; then
        extra+=(-f docker-compose.clickhouse.yml)
    fi
    # Packaged mode: no source bind-mounts (docs/packaging.md, "Packaged
    # mode"). Last, so its !override wins over the ClickHouse overlay.
    if [ "${SMOKING_PI_PACKAGED:-0}" = 1 ] && [ -f "$EDITION_DIR/docker-compose.packaged.yml" ]; then
        extra+=(-f docker-compose.packaged.yml)
    fi
    # 9<&-: the lock is this command's, not something a child could keep.
    ( cd "$EDITION_DIR" && COMPOSE_PROFILES="$p" docker compose "${extra[@]}" "$@" 9<&- )
}

compose_up() {
    # `compose up`, then put right what an interrupted or raced recreate
    # left behind (repair_temp_names). The lock keeps two smoking-pi
    # commands apart; a `docker compose` typed by hand is not under it. When
    # `up` fails while a container of the project carries a temporary name,
    # that is such a race: wait for the other run, repair, try once more.
    local st=0
    compose_exec up "$@" || st=$?
    if [ "$st" -ne 0 ] && [ -n "$(temp_named)" ]; then
        echo "Compose failed while a container was being recreated: another Compose run was changing this stack at the same time. Waiting for it, then trying once more." >&2
        for _ in $(seq 30); do
            [ -n "$(temp_named)" ] || break
            sleep 2
        done
        repair_temp_names
        st=0
        compose_exec up "$@" || st=$?
    fi
    repair_temp_names
    return "$st"
}

project_containers() {
    # "name state" per container of the project, stopped ones included.
    local proj
    proj="$(project)" || proj=""
    [ -n "$proj" ] || return 0
    docker ps -a --filter "label=com.docker.compose.project=$proj" \
        --format '{{.Names}} {{.State}}' 2>/dev/null || true
}

temp_named() {
    # Containers under Compose's temporary recreate name, "<12 hex>_<name>".
    project_containers | grep -E '^[0-9a-f]{12}_' || true
}

repair_temp_names() {
    # A recreate that did not reach its rename leaves the new container as
    # "<old id>_<name>". Compose does not fix it later -- the next `up` says
    # "Running" -- and everything that finds a container by name (docker
    # exec pro-postgres-1, the doctor) misses it. Reproduced with two
    # concurrent `up`s on Compose v2.38.2: both replaced services stayed
    # under their temporary names. Rename it when its name is free; remove
    # it when it never started and the name is taken (the service runs under
    # its name; the next `up` recreates it if it is out of date); anything
    # else is for a person to decide.
    local listed name state final
    listed="$(project_containers)"
    [ -n "$listed" ] || return 0
    while read -r name state; do
        [[ "$name" =~ ^[0-9a-f]{12}_(.+)$ ]] || continue
        final="${BASH_REMATCH[1]}"
        if ! awk -v n="$final" '$1 == n {f=1} END {exit !f}' <<<"$listed"; then
            if docker rename "$name" "$final" >/dev/null; then
                echo "Renamed $name to $final (a Compose recreate had stopped before its last step)."
            else
                echo "warning: could not rename $name to $final (docker ps -a)" >&2
            fi
        elif [ "$state" = created ]; then
            if docker rm "$name" >/dev/null; then
                echo "Removed $name, a recreate's copy that never started ($final is the service's container)."
            else
                echo "warning: could not remove $name (docker ps -a)" >&2
            fi
        else
            echo "warning: $name ($state) and $final both exist, left by an interrupted Compose recreate. Keep one: docker rm -f <the other>, then smoking-pi up" >&2
        fi
    done <<<"$listed"
}

project() {
    # The Compose project name decides the volume names (pro_postgres-data)
    # and the labels; ask Compose rather than assume the directory name, so
    # a COMPOSE_PROJECT_NAME in the env file is honored.
    compose config --format json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])'
}

volumes() {
    # The volumes the ACTIVE services mount, by their Docker names -- the
    # set `backup` copies and `purge` deletes. Not "every volume with this
    # project's label": the base file declares the ClickHouse volumes with
    # fixed names whether or not that profile is on, and the first offline
    # backup on the reference Pi spent ten minutes of downtime tarring a
    # 5.9 GB leftover from a ClickHouse trial that no running service used.
    #
    # Prints "key name" per line: the Compose key (postgres-data) and the
    # Docker name it resolves to here (pro_postgres-data, or the fixed
    # `name:` -- Basic's and Standard's volumes all have one). backup files
    # tarballs by KEY and restore resolves the name from the config it is
    # restoring INTO, so a backup moves between project names and editions'
    # fixed names without guessing from a string.
    compose config --format json 2>/dev/null | python3 -c '
import json, sys
cfg = json.load(sys.stdin)
decl = cfg.get("volumes") or {}
seen = {}
for svc in (cfg.get("services") or {}).values():
    for v in svc.get("volumes") or []:
        if v.get("type") != "volume":
            continue
        key = v["source"]
        seen.setdefault(key, (decl.get(key) or {}).get("name") or cfg["name"] + "_" + key)
for key, name in seen.items():
    print(key, name)'
}

volume_names() { volumes | cut -d" " -f2; }


volume_sizes() {
    # One `docker system df -v` for all of them: a person deciding whether
    # to stop the stack should see "5.9 GB" before, not after.
    local df; df="$(docker system df -v 2>/dev/null)"
    local v
    for v in "$@"; do
        echo "  $v  $(echo "$df" | awk -v n="$v" '$1 == n {print $NF}')"
    done
}

config_dir() { echo "${SMOKING_PI_CONFIG_DIR:-$EDITION_DIR/config-manager/config}"; }
output_dir() { echo "${SMOKING_PI_OUTPUT_DIR:-$EDITION_DIR/config-manager/output}"; }

has_service() {
    # Whether the edition defines the service at all (postgres: Standard and
    # Pro, not Basic), profiles included.
    compose config --services 2>/dev/null | grep -qx "$1"
}

remove_disabled() {
    # `up --remove-orphans` removes containers of services the compose files
    # no longer define. A service that IS defined but whose profile is off
    # is not an orphan to Compose, so its container keeps running on
    # whatever image it had: on the reference Pi, ai-insights stayed on :dev
    # for days after an upgrade moved everything else to 2.13.0-rc.3,
    # because the `ai` profile was off. Stop and remove the project's
    # containers whose service is not enabled now. Volumes are kept.
    local proj enabled
    proj="$(project)" || proj=""
    # Nothing to compare against is not "nothing is enabled": an empty list
    # here would remove the whole stack.
    enabled="$(compose config --services 2>/dev/null)" || enabled=""
    if [ -z "$proj" ] || [ -z "$enabled" ]; then
        echo "warning: Compose could not list the enabled services; containers of disabled profiles were not checked" >&2
        return 0
    fi
    local listed svc name stale=()
    if ! listed="$(docker ps -a --filter "label=com.docker.compose.project=$proj" \
                       --format '{{.Label "com.docker.compose.service"}} {{.Names}}' 2>/dev/null)"; then
        echo "warning: docker could not list the $proj containers; containers of disabled profiles were not checked" >&2
        return 0
    fi
    while read -r svc name; do
        [ -n "$name" ] || continue
        # A `compose run` one-off of an enabled service carries its label: kept.
        grep -Fqx "$svc" <<<"$enabled" || stale+=("$name")
    done <<<"$listed"
    [ "${#stale[@]}" -gt 0 ] || return 0
    echo "Removing containers of services no enabled profile runs: ${stale[*]}"
    docker stop "${stale[@]}" >/dev/null || true
    docker rm "${stale[@]}" >/dev/null || echo "warning: could not remove ${stale[*]} (docker ps -a)" >&2
}

confirm_typed() {
    # A destructive step asks for the project name, not y/n: typing "pro"
    # is a decision, Enter on a default is a reflex.
    local expected="$1" answer
    read -r -p "Type '$expected' to continue, anything else to abort: " answer || answer=""
    [ "$answer" = "$expected" ] || { echo "aborted."; exit 1; }
}

run_doctor() {
    # The doctor needs PyYAML: the package depends on python3-yaml; a clone
    # may have `uv sync`ed a venv in the module instead. Either works.
    local py=python3
    [ -x "$SMOKING_PI_HOME/shared/modules/doctor/.venv/bin/python" ] && py="$SMOKING_PI_HOME/shared/modules/doctor/.venv/bin/python"
    if [ -d "$SMOKING_PI_HOME/shared/modules/doctor" ] && "$py" -c 'import yaml' 2>/dev/null; then
        # No bytecode: under sudo it would be root's __pycache__ inside the
        # package's /opt tree, which dpkg does not own and never removes.
        ( cd "$SMOKING_PI_HOME" && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$SMOKING_PI_HOME/shared/modules/doctor${PYTHONPATH:+:$PYTHONPATH}" "$py" -m doctor --repo-root . "$@" )
    else
        echo "(doctor skipped: PyYAML not installed -- apt install python3-yaml)"
    fi
}

env_value() {
    # One key from the env file, quotes stripped; empty when absent.
    [ -f "$ENV_FILE" ] && sed -n "s/^$1=//p" "$ENV_FILE" | tail -1 | tr -d "\"'" || true
}

ssh_client() {
    # The address of the computer this session is typed from, when it is
    # an SSH session. SSH_CONNECTION is the direct answer, but `sudo`
    # drops it (env_reset), and `sudo smoking-pi install` is the packaged
    # way in; `who -m` reads the terminal's utmp entry, which sudo keeps.
    local c=""
    if [ -n "${SSH_CONNECTION:-}" ]; then
        c="${SSH_CONNECTION%% *}"
    else
        c="$(who -m 2>/dev/null | sed -n 's/.*(\(.*\)).*/\1/p' | head -1)"
        # A local X/Wayland session reports its display (":0"), not a host.
        case "$c" in :*|"") c="" ;; esac
    fi
    echo "$c"
}

host_address() {
    # The address to print in a URL, in order of how sure we are that it
    # reaches this machine from where the person is sitting:
    #  1. Over SSH: the address their client connected to, proven to work
    #     from that computer a moment ago -- or, under sudo, the source
    #     address this machine uses to reach that client, which is the
    #     same interface seen from the other side.
    #  2. The source address of the default route: what the LAN sees.
    #  3. localhost: nothing better is known.
    # Not `hostname -I | awk '{print $1}'` (show-passwords.sh's choice):
    # that is whichever interface the kernel listed first, and on a Pi
    # running Docker and Tailscale it can be a bridge or the tailnet.
    # `ip route get` is a lookup in the routing table; it sends nothing.
    local a="" client
    if [ -n "${SSH_CONNECTION:-}" ]; then
        a="$(echo "$SSH_CONNECTION" | awk '{print $3}')"
    else
        client="$(ssh_client)"
        # utmp can hold a resolved hostname rather than an address (sshd's
        # UseDNS); `ip route get` takes only addresses, so a name falls
        # through to the default route below, which is usually the same
        # interface anyway.
        [ -n "$client" ] && a="$(ip route get "$client" 2>/dev/null | sed -n 's/.* src \([^ ]*\).*/\1/p' | head -1)"
    fi
    # Loopback is no answer, and a link-local v6 address needs a zone id
    # that browsers do not accept in a URL.
    case "$a" in 127.*|::1|fe80:*|FE80:*) a="" ;; esac
    # IPv4 first, even when the SSH session came in over v6: Pro publishes
    # the web admin as 0.0.0.0:8080, which Docker binds on v4 only, so a v6
    # URL there gets no answer (seen on the reference Pi). v6 is the answer
    # only on a host with no v4 route at all.
    case "$a" in
        ""|*:*) a="$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -1 | grep . || echo "$a")" ;;
    esac
    case "$a" in "") echo localhost ;; *:*) echo "[$a]" ;; *) echo "$a" ;; esac
}

http_answers() {
    # Whether anything answers HTTP at $1: any status at all counts (the
    # web admin answers 302 to its login page). curl prints 000 when
    # nothing is listening or the connection times out.
    local code
    code="$(curl -s -o /dev/null -m 3 -w '%{http_code}' "$1" 2>/dev/null || true)"
    [ -n "$code" ] && [ "$code" != 000 ]
}

# The port of the page a person opens first, as each edition's compose file
# maps it: Pro's web admin is fixed there, the others read the env file.
# Basic has no web admin, so it is SmokePing's own page.
ui_port() {
    local port=""
    case "$SMOKING_PI_EDITION" in
        basic) port="$(env_value SMOKEPING_PORT)" ;;
        standard) port="$(env_value WEB_ADMIN_PORT)" ;;
    esac
    echo "${port:-8080}"
}
