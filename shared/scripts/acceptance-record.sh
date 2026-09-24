#!/usr/bin/env bash
#
# Print the Validation block of docs/release-acceptance.md ("The record"),
# with everything this host can answer filled in: the tag and commit, the
# Pi model, the OS, kernel and architecture, each container's image,
# restart count and uptime, and the doctor's summary. What only a person
# can judge (detections, panels, the upgrade timing) stays <fill>.
#
# Read-only: it runs `docker inspect` and `smoking-pi doctor --live`, and
# prints no secret. Run it on the reference Pi, from the checkout:
#
#   shared/scripts/acceptance-record.sh [--project pro] [--no-doctor]
#
# Why a script: the checklist used to say "run uname -r and cat
# /etc/os-release for the record", and a record typed by hand is where the
# model, the Debian release and an image left on :dev go missing.

set -euo pipefail

PROJECT=pro
RUN_DOCTOR=1
while [ $# -gt 0 ]; do
    case "$1" in
        --project) PROJECT=${2:?--project needs a Compose project name}; shift 2 ;;
        --no-doctor) RUN_DOCTOR=0; shift ;;
        -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

REPO=$(cd "$(dirname "$0")/../.." && pwd)

# ── the code ────────────────────────────────────────────────────────────
commit=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo "<not a git checkout>")
tag=$(git -C "$REPO" describe --tags --exact-match 2>/dev/null \
      || git -C "$REPO" describe --tags 2>/dev/null || echo "<no tag>")

# ── the host ────────────────────────────────────────────────────────────
model="<unknown model>"
[ -r /proc/device-tree/model ] && model=$(tr -d '\0' < /proc/device-tree/model)
os="<unknown OS>"
if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    os=$(. /etc/os-release && echo "${PRETTY_NAME:-$NAME}")
fi
debian=""
[ -r /etc/debian_version ] && debian=" (Debian $(cat /etc/debian_version))"
arch=$(dpkg --print-architecture 2>/dev/null || uname -m)

# ── the containers ──────────────────────────────────────────────────────
# Found by their Compose label, not by name (see PR #102).
mapfile -t ids < <(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT")
if [ ${#ids[@]} -eq 0 ]; then
    echo "no containers for Compose project '$PROJECT' (try --project)" >&2
    exit 1
fi

now=$(date -u +%s)
restarts=0
not_running=()
oldest_start=$now
rows=()
declare -A tags=()
while IFS='|' read -r name image count status started; do
    name=${name#/}
    restarts=$((restarts + count))
    [ "$status" = running ] || not_running+=("$name ($status)")
    start_s=$(date -u -d "$started" +%s 2>/dev/null || echo "$now")
    [ "$start_s" -lt "$oldest_start" ] && oldest_start=$start_s
    tag_of=${image##*:}
    [ "$tag_of" = "$image" ] && tag_of=latest
    tags[$tag_of]=$(( ${tags[$tag_of]:-0} + 1 ))
    rows+=("$(printf '%s|%s|%s|%.1f h' "$name" "$image" "$count" \
        "$(awk -v s=$((now - start_s)) 'BEGIN{print s/3600}')")")
done < <(docker inspect --format \
    '{{.Name}}|{{.Config.Image}}|{{.RestartCount}}|{{.State.Status}}|{{.State.StartedAt}}' \
    "${ids[@]}")

# The version most containers run; anything else is worth a line, because
# an image left on :dev or :latest is not the candidate being accepted.
main_tag=""; main_n=0
for t in "${!tags[@]}"; do
    if [ "${tags[$t]}" -gt "$main_n" ]; then main_tag=$t; main_n=${tags[$t]}; fi
done
odd=()
for row in "${rows[@]}"; do
    IFS='|' read -r n img _ _ <<<"$row"
    t=${img##*:}; [ "$t" = "$img" ] && t=latest
    [ "$t" = "$main_tag" ] || odd+=("$n on :$t")
done
hours=$(awk -v s=$((now - oldest_start)) 'BEGIN{printf "%.1f", s/3600}')

# ── the doctor ──────────────────────────────────────────────────────────
doctor="<not run (--no-doctor)>"
if [ "$RUN_DOCTOR" = 1 ]; then
    cli="$REPO/packaging/smoking-pi"
    command -v smoking-pi >/dev/null 2>&1 && [ ! -x "$cli" ] && cli=smoking-pi
    # The summary is the last "N ok, N warn, N fail" line; the exit status
    # is not ours to fail on, the record reports it.
    doctor=$("$cli" doctor --live 2>&1 | grep -E '[0-9]+ ok, [0-9]+ warn' | tail -1 || true)
    [ -n "$doctor" ] || doctor="<doctor printed no summary: run it by hand>"
fi

# ── the record ──────────────────────────────────────────────────────────
cat <<EOF
## Validation

- Tag / commit: $tag / $commit — accepted as <vX.Y.Z-rc.N> (same commit)
- Artifacts: images ghcr.io/estcarisimo/smoking-pi/*:$main_tag; digests and checksum in the release's evidence file
- Release workflow: <run URL> — host 5/5, debian 4/4, upgrade from <prev>: <pass>; candidate run: <run URL>
- Reference Pi: $model, $os$debian, kernel $(uname -r), $arch
- Upgrade on the Pi from <prev>: <pass — minutes>, restarts $restarts; credentials and targets <intact>
- Reboot recovery: <pass — up in minutes>
- doctor --live: $doctor
- Data: <all enabled probes writing; Wi-Fi panel live; no radio hang since boot>
- Detections: <get_loss_events / get_microcut_stats agree with the alerter; events that day>
- Stability: $hours h since the oldest container start, restarts $restarts
- Untested this release: <clean install on Raspberry Pi OS; Basic/Standard on a Pi; ClickHouse; ai profile>
- Known limitations: <anything found and deferred, with the issue link>
- Backup used: <path>; rollback path: <which>
EOF

echo
echo "<!-- containers of project '$PROJECT': name | image | restarts | up"
for row in "${rows[@]}"; do echo "     ${row//|/ | }"; done
[ ${#odd[@]} -eq 0 ] || echo "     NOT on :$main_tag: ${odd[*]}"
[ ${#not_running[@]} -eq 0 ] || echo "     NOT running: ${not_running[*]}"
echo "-->"
