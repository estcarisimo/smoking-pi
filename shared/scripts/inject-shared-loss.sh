#!/usr/bin/env bash
#
# Prove the inference module's shared path end to end, on a STAGING Pi:
# inject a loss evening into the inference bin cache, run one pass, inject
# one cycle of widespread loss for the path meter, and check the three
# outputs in InfluxDB (shared_degradation with its attribution,
# shared_segment, trigger=episode traces). Then put everything back.
# docs/inference.md, "Proving the shared path on staging".
#
#   shared/scripts/inject-shared-loss.sh --host <this host's name> \
#       [--csv evening.csv] [--start 2026-11-09T22:00:00Z] [--hours 3] \
#       [--pause] [--keep] [--no-episode] [--wait 15] [--project pro]
#   shared/scripts/inject-shared-loss.sh --host <this host's name> --cleanup
#
# The guard: it refuses unless this host's name contains "staging", --host
# repeats it exactly (type it; the reference Pi is "smokingpi", so a
# command copied to the wrong terminal stops here), and the Docker daemon
# it talks to is this host's own (a DOCKER_HOST or a context pointing
# elsewhere stops here too). inject.py refuses to run without --staging and
# the daemon's name in INJECT_HOST, which only this script passes.
#
# While the widespread cycle is in InfluxDB the alerter is stopped (it
# would alert on it) and started again on exit, whatever happens. The
# assistant's own automations (an hourly OpenClaw heartbeat) read the same
# data: run this between their runs, or after their last one of the day.
#
# --pause waits for Enter after the checks, with every point in place, for
# screenshots of Connectivity & Quality. --keep deletes the injected loss
# cycle (what the alerter reads) and leaves the evening and its outputs in
# place; --cleanup, later, puts the rest back. --csv takes a maintainer's
# evening (bin,target,loss) instead of the synthetic one; it is copied into
# the container and never stored.

set -euo pipefail

PROJECT=pro
HOST=
CSV=
START=
HOURS=3
PAUSE=0
KEEP=0
EPISODE=1
WAIT_MIN=15
ALLOW_ROUTER=0
ONLY_CLEANUP=0
while [ $# -gt 0 ]; do
    case "$1" in
        --host) HOST=${2:?--host needs the host name}; shift 2 ;;
        --csv) CSV=${2:?--csv needs a file}; shift 2 ;;
        --start) START=${2:?--start needs an ISO time}; shift 2 ;;
        --hours) HOURS=${2:?--hours needs a number}; shift 2 ;;
        --project) PROJECT=${2:?--project needs a Compose project name}; shift 2 ;;
        --wait) WAIT_MIN=${2:?--wait needs minutes}; shift 2 ;;
        --pause) PAUSE=1; shift ;;
        --keep) KEEP=1; shift ;;
        --no-episode) EPISODE=0; shift ;;
        --allow-router) ALLOW_ROUTER=1; shift ;;
        --cleanup) ONLY_CLEANUP=1; shift ;;
        -h|--help) awk 'NR > 1 && !/^#/ {exit} NR > 1 {sub(/^# ?/, ""); print}' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

# ── the guard ───────────────────────────────────────────────────────────
name=$(hostname)
shopt -s nocasematch
if [[ "$name" != *staging* ]]; then
    echo "refused: this host is '$name'; the injection test runs on a staging Pi only" >&2
    echo "(its name must contain 'staging'). Never on the reference Pi." >&2
    exit 2
fi
shopt -u nocasematch
if [ "$HOST" != "$name" ]; then
    echo "refused: --host must repeat this host's name exactly ('$name')" >&2
    exit 2
fi
daemon=$(docker info --format '{{.Name}}' 2>/dev/null || true)
if [ "$daemon" != "$name" ]; then
    echo "refused: the Docker daemon in use is '${daemon:-unreachable}', not this host" >&2
    echo "('$name'): unset DOCKER_HOST or switch the docker context back." >&2
    exit 2
fi

container() {
    docker ps -q --filter "label=com.docker.compose.project=$PROJECT" \
        --filter "label=com.docker.compose.service=$1" | head -n1
}
INF=$(container inference)
[ -n "$INF" ] || { echo "the inference service is not running (profile inference)" >&2; exit 2; }
PATHMETER=$(container pathmeter)
ALERTER=$(container alerter)
if [ "$EPISODE" = 1 ] && [ -z "$PATHMETER" ]; then
    echo "the path meter is not running: no episode traces to prove (or pass --no-episode)" >&2
    exit 2
fi

# An image older than this tool has no inject.py, or an older one: copy
# this checkout's in. It lives until the container is recreated.
here=$(cd "$(dirname "$0")/../modules/inference" && pwd)
if ! docker exec "$INF" cat inject.py 2>/dev/null | cmp -s - "$here/inject.py"; then
    docker cp "$here/inject.py" "$INF:/app/inject.py"
    echo "(copied this checkout's inject.py into the container)"
fi

run() { docker exec -e INJECT_HOST="$daemon" "$INF" python inject.py --staging "$@"; }

restart_alerter() {
    if [ -n "$ALERTER" ] && [ "$(docker inspect -f '{{.State.Running}}' "$ALERTER")" != true ]; then
        docker start "$ALERTER" >/dev/null && echo "alerter started again"
    fi
}

if [ "$ONLY_CLEANUP" = 1 ]; then
    # Also after a run that was killed, or a reboot, with the alerter stopped.
    restart_alerter
    run cleanup || { echo "cleanup failed: run --cleanup again" >&2; exit 1; }
    exit
fi

# One test at a time: refuse before the trap below, which would clean up
# the other test's state.
if docker exec "$INF" test -f /var/lib/inference/inject/manifest.json; then
    echo "refused: a test is already under way; finish it with: $0 --host $HOST --cleanup" >&2
    exit 2
fi

args=(--hours "$HOURS")
[ -n "$START" ] && args+=(--start "$START")
[ "$ALLOW_ROUTER" = 1 ] && args+=(--allow-router)
if [ -n "$CSV" ]; then
    docker cp "$CSV" "$INF:/tmp/inject-evening.csv"
    args+=(--csv /tmp/inject-evening.csv)
fi

cleanup() {
    local code=$? failed=0
    set +e
    if [ "$KEEP" = 1 ]; then
        echo "== --keep: deleting the injected loss cycle, keeping the evening and its outputs"
        run cleanup --keep-points || failed=1
        echo "   finish with: $0 --host $HOST --cleanup"
    else
        echo "== cleanup"
        run cleanup || failed=1
    fi
    # The alerter only after the injected cycle is gone: it would alert on it.
    restart_alerter
    [ -n "$CSV" ] && docker exec "$INF" rm -f /tmp/inject-evening.csv
    if [ "$failed" = 1 ]; then
        echo "CLEANUP FAILED: staging is not restored; run: $0 --host $HOST --cleanup" >&2
        exit 1
    fi
    exit "$code"
}
# Set before inject: a refused or interrupted inject still removes the CSV
# and puts back whatever it changed (cleanup with no test restores the copy).
trap cleanup EXIT

echo "== plan and inject"
run inject "${args[@]}"

echo "== one pass"
run pass

if [ "$EPISODE" = 1 ]; then
    if [ -n "$ALERTER" ]; then
        echo "== stopping the alerter while the widespread cycle is in InfluxDB"
        docker stop "$ALERTER" >/dev/null
    fi
    echo "== widespread cycle"
    run widespread
    echo "== waiting up to $WAIT_MIN min for the path meter's episode round"
    deadline=$(( $(date +%s) + WAIT_MIN * 60 ))
    until run verify --episode-only; do
        [ "$(date +%s)" -lt "$deadline" ] || { echo "no episode trace in $WAIT_MIN min"; break; }
        sleep 30
    done
fi

echo "== verify"
status=0
if [ "$EPISODE" = 1 ]; then run verify || status=$?; else run verify --no-episode || status=$?; fi

if [ "$PAUSE" = 1 ]; then
    read -r -p "Every point is in place. Press Enter to clean up... " _ </dev/tty
fi
exit "$status"
