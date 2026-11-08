#!/usr/bin/env bash
#
# Ask the OpenClaw agent every question in the assistant's question bank
# (shared/modules/mcp-server/evals/questions.yaml), one at a time, and
# write a Markdown report: per question, the MCP tools the agent called
# (the server's own log lines inside that turn), every check the answer
# failed, and the answer itself. docs/openclaw-integration.md, "Checking
# the assistant".
#
# Maintainer tool: run it on a Pi with the OpenClaw gateway and the Pro
# stack, as the user that runs `openclaw`. Each question is its own agent
# session (nothing carries over between questions) and is never delivered
# to a chat. It does act: `mute-cpe` really mutes the gateway's alerts for
# two hours, and the end of the run says how to lift it.
#
#   shared/scripts/assistant-eval.sh [--out DIR] [--only id,id] [--zone ZONE]
#       [--judgments FILE] [--title TEXT] [--timeout SECONDS] [--min-free-mb MB]
#       [--retries N]
#
# --out keeps the raw answers and logs (default: a new directory under
# /tmp); the report is <out>/report.md. --zone is the messages' zone the
# answers are checked against (default: the MCP server's DIGEST_TZ, else
# its TZ). Re-scoring needs no agent: run evals/report.py on <out>/raw.
# A turn the agent never answered (a gateway or model error, which says
# nothing about the guide) is asked again --retries times (default 1), a
# minute later, in a new session; what is still unanswered is marked so in
# the report. Asking again later with --only and the same --out replaces
# just those questions. A model account out of quota stops the run at
# that question, since every later one would fail the same way.
#
# The Pi is small: before each question it waits for MemAvailable to reach
# --min-free-mb (default 400), and it never starts a turn within four
# minutes of :00 or :30, where the gateway's own heartbeats run and would
# put their tool calls in the window.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
EVALS="$ROOT/shared/modules/mcp-server/evals"
BANK="$EVALS/questions.yaml"
OUT=""
ONLY=""
ZONE=""
JUDGMENTS=""
TITLE="Assistant question bank"
TIMEOUT=420
MIN_FREE_MB=400
RETRIES=1
CONTAINER=${MCP_CONTAINER:-smokeping-mcp-server}
AGENT=${OPENCLAW_AGENT:-main}

while [ $# -gt 0 ]; do
    case "$1" in
        --out) OUT=${2:?--out needs a directory}; shift 2 ;;
        --only) ONLY=${2:?--only needs ids, comma-separated}; shift 2 ;;
        --zone) ZONE=${2:?--zone needs a zone, e.g. America/Chicago}; shift 2 ;;
        --judgments) JUDGMENTS=${2:?--judgments needs a file}; shift 2 ;;
        --title) TITLE=${2:?--title needs text}; shift 2 ;;
        --timeout) TIMEOUT=${2:?--timeout needs seconds}; shift 2 ;;
        --min-free-mb) MIN_FREE_MB=${2:?--min-free-mb needs a number}; shift 2 ;;
        --retries) RETRIES=${2:?--retries needs a number}; shift 2 ;;
        -h|--help) awk 'NR > 1 && !/^#/ {exit} NR > 1 {sub(/^# ?/, ""); print}' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

command -v openclaw >/dev/null 2>&1 || {
    echo "No openclaw on PATH (an nvm install needs its bin dir on PATH)." >&2; exit 2; }
docker inspect "$CONTAINER" >/dev/null 2>&1 || {
    echo "No $CONTAINER container here: this runs on the Pi with the Pro stack." >&2; exit 2; }
python3 -c 'import yaml' 2>/dev/null || {
    echo "The report needs PyYAML: sudo apt install python3-yaml" >&2; exit 2; }

if [ -z "$ZONE" ]; then
    env_of() { docker inspect "$CONTAINER" --format '{{range .Config.Env}}{{println .}}{{end}}' \
        | sed -n "s/^$1=//p" | head -1; }
    ZONE="$(env_of DIGEST_TZ)"
    [ -n "$ZONE" ] || ZONE="$(env_of TZ)"
    [ -n "$ZONE" ] || ZONE=UTC
fi

[ -n "$OUT" ] || OUT="$(mktemp -d /tmp/assistant-eval.XXXXXX)"
mkdir -p "$OUT/raw"
RUN="eval-$(date +%Y%m%d-%H%M%S)"

free_mb() { awk '/^MemAvailable:/ {print int($2 / 1024)}' /proc/meminfo; }

wait_for_room() {
    local waited=0
    while [ "$(free_mb)" -lt "$MIN_FREE_MB" ]; do
        [ "$waited" = 0 ] && echo "  waiting for memory ($(free_mb) MB free, want $MIN_FREE_MB)"
        sleep 20; waited=$((waited + 20))
        if [ "$waited" -ge 600 ]; then
            echo "  still $(free_mb) MB free after 10 minutes; asking anyway" >&2; return
        fi
    done
}

clear_of_heartbeats() {
    # Minutes 26-30 and 56-00 are the four before a heartbeat; wait out the
    # beat itself too, so its calls land before our window opens.
    local m
    m=$((10#$(date +%M) % 30))
    if [ "$m" -ge 26 ]; then
        local wait_s=$(( (30 - m) * 60 + 90 - 10#$(date +%S) ))
        echo "  a heartbeat is due; waiting ${wait_s}s"
        sleep "$wait_s"
    elif [ "$m" -lt 1 ]; then
        sleep $(( 90 - 10#$(date +%S) ))
    fi
}

asked=0
while IFS=$'\t' read -r id ask; do
    if [ -n "$ONLY" ] && [[ ",$ONLY," != *",$id,"* ]]; then
        continue
    fi
    asked=$((asked + 1))
    echo "[$asked] $id: $ask"
    try=0
    while :; do
        wait_for_room
        clear_of_heartbeats
        start=$(date +%s)
        rc=0
        timeout "$TIMEOUT" openclaw agent --agent "$AGENT" --session-key "$RUN-$id-$try" --json \
            --message "$ask" >"$OUT/raw/$id.answer.json" 2>"$OUT/raw/$id.stderr" || rc=$?
        end=$(( $(date +%s) + 1 ))
        if grep -qi 'usage limit' "$OUT/raw/$id.answer.json"; then
            # The model's account is out of quota: every question after this
            # one would fail the same way and push the reset further out.
            echo "    the agent's model account hit its usage limit; stopping here:"
            grep -oi '"message": *"[^"]*' "$OUT/raw/$id.answer.json" | head -1 | sed 's/^/    /'
            echo "start=$start end=$end rc=$rc" >"$OUT/raw/$id.meta"
            break 2
        fi
        [ "$rc" != 0 ] && [ "$try" -lt "$RETRIES" ] || break
        # Only a turn that never answered: its JSON has no payload text.
        python3 "$EVALS/report.py" --answered "$OUT/raw/$id.answer.json" && break
        try=$((try + 1))
        echo "    no answer (openclaw exited $rc); asking again in a minute"
        sleep 60
    done
    # Tool calls log when they return; give the last one a moment.
    sleep 2
    docker logs --timestamps --since "$((start - 5))" --until "$((end + 5))" "$CONTAINER" \
        2>&1 | grep 'mcp.tools' >"$OUT/raw/$id.log" || true
    echo "start=$start end=$end rc=$rc" >"$OUT/raw/$id.meta"
    echo "    $(grep -c 'tool=' "$OUT/raw/$id.log" || true) tool call(s), rc=$rc, $((end - start))s"
done < <(python3 "$EVALS/report.py" --bank "$BANK" --list)

args=(--bank "$BANK" --raw "$OUT/raw" --zone "$ZONE" --title "$TITLE")
[ -z "$JUDGMENTS" ] || args+=(--judgments "$JUDGMENTS")
python3 "$EVALS/report.py" "${args[@]}" >"$OUT/report.md"
echo
grep -m1 '^\*\*' "$OUT/report.md"
echo "Report: $OUT/report.md (raw answers and logs in $OUT/raw)"
if [ -z "$ONLY" ] || [[ ",$ONLY," == *",mute-cpe,"* ]]; then
    echo "mute-cpe muted the gateway's alerts for two hours: ask the agent to"
    echo "unmute them, or let the mute expire (list_alert_state shows it)."
fi
