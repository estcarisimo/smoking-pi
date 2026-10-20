# shellcheck shell=bash
# smoking-pi lifecycle: `restart`, `upgrade`, `backup`, `restore`, `purge`.
# Sourced by cli/smoking-pi, never run on its own.

cmd_restart() {
    # In place: `compose restart` does not recreate, so a changed image,
    # compose file or env file needs `down` + `up`. A whole-stack restart on
    # Pro with InfluxDB then checks that Grafana's token is one InfluxDB
    # accepts (sync-influx-token.sh: a no-op when it is, and it only ever
    # writes a token InfluxDB has proven it takes). Their divergence shows
    # as empty InfluxDB panels while the data keeps arriving. Before this
    # command, only shared/scripts/manage-containers.sh did it.
    compose restart "$@"
    if [ $# -eq 0 ] && [ -x "$EDITION_DIR/sync-influx-token.sh" ] && profile_on influxdb; then
        # `restart` returns once the containers run, not once influxd
        # answers; asked too early, the script reads "no usable token".
        local i
        for i in $(seq 1 45); do
            compose exec -T influxdb influx ping >/dev/null 2>&1 && break
            sleep 2
        done
        if ! compose exec -T influxdb influx ping >/dev/null 2>&1; then
            echo "warning: InfluxDB did not answer within 90 s; its token was not checked ($(cli_name) logs influxdb)" >&2
        else
            "$EDITION_DIR/sync-influx-token.sh" || echo "warning: the InfluxDB token check failed; Grafana's InfluxDB panels may be empty (see above)" >&2
        fi
    fi
}

# Dashboards a release retired, by uid. Provisioning never deletes one
# (disableDeletion: true, so an edit to the bind-mounted tree cannot wipe
# a dashboard with months of a person's history behind it); when its file
# is gone Grafana only unprovisions it, and it stays in the list pointing
# at the old generation until someone deletes it. Done here, once per
# upgrade. A uid that is already gone is not an error.
RETIRED_DASHBOARDS="smokeping-lat-pct-v28 smokeping-dns-resolvers-v4 smokeping-lat-compare-v2 smokeping-lat-pct-clickhouse-v1 smokeping-lat-compare-clickhouse-v1 smokeping-dns-resolvers-clickhouse-v1 top_sites-side-by-side-v1 netflix_oca-side-by-side-v1 custom-side-by-side-v1 topsites-side-by-side-clickhouse-v1 netflix-side-by-side-clickhouse-v1 custom-side-by-side-clickhouse-v1"
# Folders a release stopped provisioning (the directory names of the old
# tree, and four an older provisioning scheme left on the reference Pi).
# Provisioning moves a dashboard to its new folder and leaves the old one
# behind, empty. Deleted only when EMPTY: a folder holding anything -- a
# person's own dashboard, the ClickHouse tree's "SmokePing" parent -- stays.
RETIRED_FOLDERS="home|overview|cpe|quality|wifi|side-by-side-pings|individual-pings|target-detail|http-by-version|dns-resolution-times|dns-wizard|SmokePing|Side-by-Side Pings|Individual Pings|DNS Resolution Times"

retire_dashboards() {
    has_service grafana || return 0
    local password user; password="$(env_get GF_SECURITY_ADMIN_PASSWORD)"
    [ -n "$password" ] || return 0
    user="$(env_get GF_SECURITY_ADMIN_USER)"; user="${user:-admin}"
    # Grafana unprovisions a dashboard whose file disappeared on its first
    # scan after start, which can land after the API answers: 400 (still
    # provisioned) and 000 (not listening yet) are retried, one minute in
    # all so a Grafana that never comes up does not hold the upgrade.
    local uid code gone=0 kept="" budget=12
    for uid in $RETIRED_DASHBOARDS; do
        while :; do
            # Credentials on stdin (-K -), never in argv: see cli/lib/openclaw.sh.
            # curl prints its write-out (000) AND exits non-zero when nothing
            # listens, so no `|| echo 000` here: that made "000000", which
            # matched no case and skipped the retry. Empty (no curl) is 000.
            code="$(printf 'url = "http://127.0.0.1:3000/api/dashboards/uid/%s"\nrequest = "DELETE"\nsilent\noutput = "/dev/null"\nwrite-out = "%%{http_code}"\nuser = "%s:%s"\n' \
                "$uid" "$(curl_cfg_quote "$user")" "$(curl_cfg_quote "$password")" \
                | curl -K - 2>/dev/null)" || true
            code="${code:-000}"
            case "$code" in
                200) gone=$((gone + 1)); break ;;
                404) break ;;
                400|000) budget=$((budget - 1)); [ "$budget" -gt 0 ] || { kept="$kept $uid($code)"; break; }; sleep 5 ;;
                *) kept="$kept $uid($code)"; break ;;
            esac
        done
    done
    [ "$gone" = 0 ] || echo "Removed $gone retired dashboard(s) from Grafana."
    [ -z "$kept" ] || echo "Could not remove retired dashboard(s), with Grafana's answer:$kept. They can be deleted from Grafana's dashboard list." >&2
    return 0
}

grafana_api() {
    # $1 method, $2 path: status on stdout's last line, body before it.
    # Credentials on stdin (-K -), never in argv: see cli/lib/openclaw.sh.
    printf 'url = "http://127.0.0.1:3000%s"\nrequest = "%s"\nsilent\nwrite-out = "\\n%%{http_code}"\nuser = "%s:%s"\n' \
        "$(curl_cfg_quote "$2")" "$1" "$(curl_cfg_quote "$GRAFANA_USER")" "$(curl_cfg_quote "$GRAFANA_PASSWORD")" \
        | curl -K - 2>/dev/null || true
}

retire_folders() {
    has_service grafana || return 0
    command -v python3 >/dev/null || return 0
    # Dynamically scoped: grafana_api, called from here, reads them; nothing
    # after this function does.
    local GRAFANA_USER GRAFANA_PASSWORD
    GRAFANA_PASSWORD="$(env_get GF_SECURITY_ADMIN_PASSWORD)"; [ -n "$GRAFANA_PASSWORD" ] || return 0
    GRAFANA_USER="$(env_get GF_SECURITY_ADMIN_USER)"; GRAFANA_USER="${GRAFANA_USER:-admin}"
    local listing status pairs uid title answer gone=0 try=0
    # Grafana may still be starting (retire_dashboards usually absorbed that
    # wait); half a minute, then the folders go on the next upgrade.
    while :; do
        listing="$(grafana_api GET /api/folders)"; status="${listing##*$'\n'}"
        [ "$status" = 200 ] && break
        try=$((try + 1)); [ "$try" -lt 6 ] || return 0
        sleep 5
    done
    # "uid<TAB>title" per top-level folder whose title is retired.
    pairs="$(printf '%s' "${listing%$'\n'*}" | RETIRED_FOLDERS="$RETIRED_FOLDERS" python3 -c '
import json, os, sys
retired = set(os.environ["RETIRED_FOLDERS"].split("|"))
for f in json.load(sys.stdin):
    if f.get("title") in retired and f.get("uid"):
        print(f["uid"] + "\t" + f["title"])' 2>/dev/null)" || return 0
    while IFS=$'\t' read -r uid title; do
        [ -n "$uid" ] || continue
        # Anything inside -- a dashboard, a subfolder -- and the folder stays.
        answer="$(grafana_api GET "/api/search?folderUIDs=$uid&limit=1")"
        [ "${answer##*$'\n'}" = 200 ] && [ "${answer%$'\n'*}" = "[]" ] || continue
        answer="$(grafana_api DELETE "/api/folders/$uid")"
        [ "${answer##*$'\n'}" = 200 ] && gone=$((gone + 1))
    done <<< "$pairs"
    [ "$gone" = 0 ] || echo "Removed $gone empty folder(s) of retired dashboards from Grafana."
    return 0
}

# The images of releases this host no longer runs. Every `upgrade` pulls a
# new set of thirteen images and left every previous set on disk: by
# v2.38.2 the reference Pi carried 513 Smoking Pi images, 92 GB of a
# 117 GB card, and the pull that filled it took PostgreSQL (crash loop on
# postmaster.pid), Grafana (SQLite "database or disk is full") and
# InfluxDB (WAL writes refused until the container was restarted, 16
# minutes of measurements lost) down with it. Per image, by name, never
# `docker image prune -a`: that would take cloudflared and whatever else
# the person runs on the same daemon. Only images of this project's
# registry, only tags other than the one just brought up, and never one a
# container (running or stopped) still references; `image rm` without
# `-f` refuses those anyway. Called once the new containers run and are
# still running ten seconds later, so a failed upgrade keeps the previous
# release's images to go back to.
remove_old_images() {
    local registry="${SMOKING_PI_REGISTRY:-ghcr.io/estcarisimo/smoking-pi}"
    local keep="${SMOKING_PI_VERSION:-dev}"
    local listing name state pass
    # "name state" per container of the project, twice, ten seconds apart:
    # `up -d` returns as soon as the containers start, and a release that
    # crashes on its first request is still "running" at that instant. A
    # container that is not running is the previous release's fallback,
    # kept intact.
    for pass in 1 2; do
        [ "$pass" = 1 ] || sleep 10
        listing="$(project_containers)"
        while read -r name state; do
            [ -n "$name" ] || continue
            if [ "$state" != running ]; then
                echo "Keeping the images of the previous release: $name is $state, not running ($(cli_name) status)." >&2
                return 0
            fi
        done <<< "$listing"
    done
    local used images ref id size removed=0 bytes=0 kept=""
    # What any container references, by name or by id: skipped before docker
    # is asked, so the log holds no refusals.
    used="$(docker ps -a --format '{{.Image}}' 2>/dev/null || true)"
    # "repo:tag id size" per image of the registry; <none> is a tag whose
    # layers a pull replaced (an image rm names it by id).
    images="$(docker image ls --filter "reference=$registry/*" \
        --format '{{.Repository}}:{{.Tag}} {{.ID}} {{.Size}}' 2>/dev/null || true)"
    while read -r ref id size; do
        [ -n "$ref" ] || continue
        [ "${ref##*:}" != "$keep" ] || continue
        case "$ref" in *":<none>") ref="$id" ;; esac
        if grep -qxF -e "$ref" -e "$id" <<< "$used"; then continue; fi
        if docker image rm "$ref" >/dev/null 2>&1; then
            removed=$((removed + 1)); bytes=$((bytes + $(size_bytes "$size")))
        else
            kept="$kept $ref"
        fi
    done <<< "$images"
    if [ "$removed" = 0 ]; then
        echo "No images of other releases to remove."
    else
        echo "Removed $removed image(s) of other releases, up to $(human_bytes "$bytes") (their sizes added; layers shared with the $keep images stay)."
    fi
    [ -z "$kept" ] || echo "Could not remove image(s):$kept (docker image rm refused; a container may still use them)." >&2
    return 0
}

size_bytes() {
    # "92.1MB", "1.2GB", "512kB", "0B" (docker's units are decimal) -> bytes.
    awk -v s="$1" 'BEGIN {
        n = s; sub(/[^0-9.].*$/, "", n); u = s; sub(/^[0-9.]*/, "", u)
        m["B"] = 1; m["kB"] = 1e3; m["MB"] = 1e6; m["GB"] = 1e9; m["TB"] = 1e12
        printf "%.0f", n * (u in m ? m[u] : 1) }'
}

human_bytes() {
    awk -v b="$1" 'BEGIN {
        if (b >= 1e9) printf "%.1f GB", b / 1e9
        else if (b >= 1e6) printf "%.0f MB", b / 1e6
        else printf "%d kB", b / 1e3 }'
}

# The upgrade's own window, in the measurements. An upgrade recreates the
# containers, the Wi-Fi radio and the first hop with them, and showed up in
# the data as a cut on every destination at once, the CPE gateway included
# (reference Pi, 2026-10-15 17:30Z: 4-6 % loss everywhere; 2026-10-18
# 18:30Z: 66 %). Nothing said an upgrade had happened, so the detectors and
# whoever read the charts took it for the network. Two points in the
# edition's InfluxDB bucket, measurement `upgrade`: phase=start before the
# first container is touched, phase=end (result=ok|failed) once InfluxDB
# answers again -- or from the exit trap when the upgrade fails. Tags
# from_version and to_version on both; field value=1. shared/modules/inference
# leaves the windows out of its shared passes; Grafana draws them
# (docs/upgrades.md). Through the database
# container's own CLI, as the watchdog records its events (cli/lib/watchdog.sh):
# no token on this side. Best effort: no InfluxDB (Basic, Standard,
# ClickHouse), nothing written and nothing said; InfluxDB not answering,
# one line and the upgrade goes on.
UPGRADE_MARK=""         # "from to" once the start point is in; empty: no end to write
UPGRADE_MARK_TRIES=20   # end point: tries, three seconds apart (InfluxDB restarts),
UPGRADE_MARK_WAIT=60    # and no new try after this many seconds, however long each took

upgrade_marks_wanted() {
    [ "$SMOKING_PI_EDITION" = pro ] && profile_on influxdb
}

bounded() {
    # timeout(1) where there is one. macOS has none: there the call runs
    # unbounded, and only the end point's deadline below still holds.
    if command -v timeout >/dev/null 2>&1; then timeout "$@"; else shift; "$@"; fi
}

influx_container() {
    # "id image" of this stack's InfluxDB container, running; empty when none.
    bounded 5 docker ps --filter "label=com.docker.compose.project.working_dir=$EDITION_DIR" \
        --filter label=com.docker.compose.service=influxdb --format '{{.ID}} {{.Image}}' 2>/dev/null | head -1 || true
}

mark_tag() {
    # A line-protocol tag value: anything but a version's characters becomes
    # `_`, so a space or a comma cannot split the line. Empty: "unknown".
    local v; v="$(printf '%s' "$1" | tr -c 'A-Za-z0-9._+-' '_')"
    printf '%s' "${v:-unknown}"
}

mark_now() {
    # Nanoseconds; date without %N (macOS) gives whole seconds.
    local n; n="$(date +%s%N)"
    case "$n" in *[!0-9]*) n="$(date +%s)000000000" ;; esac
    printf '%s' "$n"
}

influx_write_line() {
    # $1: one line of line protocol. Status: whether InfluxDB took it.
    local c; c="$(influx_container)"
    [ -n "$c" ] || return 1
    printf '%s\n' "$1" | bounded 10 docker exec -i "${c%% *}" sh -c \
        'influx write --org "$DOCKER_INFLUXDB_INIT_ORG" --bucket "$DOCKER_INFLUXDB_INIT_BUCKET" -t "$DOCKER_INFLUXDB_INIT_ADMIN_TOKEN" --precision ns' \
        >/dev/null 2>&1
}

upgrade_mark_start() {
    upgrade_marks_wanted || return 0
    # The version running now: the tag of InfluxDB's image (every image of
    # a release carries the release's tag; a clone's own build is "dev").
    local c from to line
    c="$(influx_container)"
    from="${c#* }"; from="${from##*/}"
    # name:tag gives the tag; a name@sha256:... digest or a bare id, nothing.
    case "$from" in *@*) from="" ;; *:*) from="${from##*:}" ;; *) from="" ;; esac
    from="$(mark_tag "$from")"; to="$(mark_tag "${SMOKING_PI_VERSION:-dev}")"
    line="$(printf 'upgrade,phase=start,from_version=%s,to_version=%s value=1 %s' "$from" "$to" "$(mark_now)")"
    # One try: InfluxDB is running before the upgrade or it is not, and the
    # upgrade does not wait for it.
    if influx_write_line "$line"; then
        UPGRADE_MARK="$from $to"
        trap 'upgrade_mark_end failed' EXIT
        trap 'exit 130' INT
        trap 'exit 143' TERM
    else
        echo "InfluxDB did not take the upgrade's start point; this upgrade's window is not recorded (docs/upgrades.md)." >&2
    fi
    return 0
}

upgrade_mark_end() {
    # $1: ok | failed. Stamped now, written when InfluxDB answers again:
    # a recreated InfluxDB takes tens of seconds, so retries for up to a
    # minute by the clock (a wedged Docker makes each try last up to 15 s,
    # and twenty of those would be five), then one line and on.
    [ -n "$UPGRADE_MARK" ] || return 0
    trap - EXIT INT TERM
    local from="${UPGRADE_MARK% *}" to="${UPGRADE_MARK#* }" line try=1 deadline=$((SECONDS + UPGRADE_MARK_WAIT))
    UPGRADE_MARK=""
    line="$(printf 'upgrade,phase=end,from_version=%s,to_version=%s,result=%s value=1 %s' "$from" "$to" "$1" "$(mark_now)")"
    until influx_write_line "$line"; do
        if [ "$try" -ge "$UPGRADE_MARK_TRIES" ] || [ "$SECONDS" -ge "$deadline" ]; then
            echo "InfluxDB did not take the upgrade's end point within a minute; its window shows no end (docs/upgrades.md)." >&2
            return 0
        fi
        try=$((try + 1)); sleep 3
    done
    return 0
}

cmd_upgrade() {
    local doctor=1
    while [ $# -gt 0 ]; do
        case "$1" in
            --skip-doctor) doctor=0; shift ;;
            *) echo "unknown option $1" >&2; exit 2 ;;
        esac
    done
    need_edition
    [ -f "$ENV_FILE" ] || { echo "no env file at $ENV_FILE: nothing installed to upgrade" >&2; exit 1; }
    echo "Upgrading the $SMOKING_PI_EDITION edition ($(installed_version))."
    echo "A PostgreSQL or InfluxDB major is a data migration: docs/upgrades.md, and 'smoking-pi backup' first."
    if [ -n "${SMOKING_PI_VERSION:-}" ]; then
        # The compose files name ghcr.io/.../<service>:$SMOKING_PI_VERSION;
        # a new package changed the version, so the pull fetches the release.
        echo "Pulling the $SMOKING_PI_VERSION images${VERSION_SOURCE:+ ($VERSION_SOURCE)}."
        compose pull || {
            # A clone can build what it checked out; a package cannot, so
            # there the likely cause is the network or the registry.
            if [ -n "$VERSION_SOURCE" ]; then
                echo "No published $SMOKING_PI_VERSION images to pull. To build this checkout instead:" >&2
                echo "  SMOKING_PI_VERSION=dev smoking-pi upgrade" >&2
            else
                echo "Could not pull the $SMOKING_PI_VERSION images from ${SMOKING_PI_REGISTRY:-ghcr.io/estcarisimo/smoking-pi}: check the network, then run upgrade again." >&2
            fi
            exit 1
        }
    else
        # A clone: the images are built here. --pull refreshes the base
        # images so a Dependabot bump in a Dockerfile actually lands.
        compose build --pull
    fi
    # The images are here; from now on the containers change.
    upgrade_mark_start
    # Only the services whose image or config changed are recreated.
    compose up -d --remove-orphans
    remove_disabled
    upgrade_mark_end ok
    retire_dashboards
    retire_folders
    publish_avahi
    # With the new release running: the images of every other one.
    remove_old_images
    # A clone installed before setup.sh linked the command gets it here.
    link_cli --quiet
    # The new release's answering guide, to the assistant that already had
    # the old one (cli/lib/openclaw.sh).
    openclaw_refresh_skill || true
    # An `if`, not `[ ] &&`: as the function's last statement a false test
    # would become its exit status, and --skip-doctor would "fail".
    if [ "$doctor" = 1 ]; then run_doctor --live; fi
}

cmd_backup() {
    local dir="" online=0
    while [ $# -gt 0 ]; do
        case "$1" in
            --online) online=1; shift ;;
            -*) echo "unknown option $1" >&2; exit 2 ;;
            *) dir="$1"; shift ;;
        esac
    done
    need_edition
    local proj; proj="$(project)"
    [ -n "$proj" ] || { echo "cannot resolve the Compose project (is the env file there?)" >&2; exit 1; }
    dir="${dir:-./smoking-pi-backup-$(date +%Y%m%d-%H%M%S)}"
    mkdir -p "$dir/volumes"
    dir="$(cd "$dir" && pwd)"
    echo "Backing up project $proj to $dir"
    {
        echo "edition=$SMOKING_PI_EDITION"; echo "project=$proj"
        echo "version=$(installed_version)"
        echo "date=$(date -u +%Y-%m-%dT%H:%M:%SZ)"; echo "online=$online"
    } > "$dir/manifest"
    # The dump is the restore path for a PostgreSQL major (docs/upgrades.md);
    # the volume tarball below is the restore path for everything else.
    if has_service postgres && grep -qx postgres <(compose ps --status running --services 2>/dev/null); then
        local pguser; pguser="$(sed -n 's/^POSTGRES_USER=//p' "$ENV_FILE" | tail -1)"
        compose exec -T postgres pg_dumpall -U "${pguser:-smokeping}" > "$dir/postgres.sql"
        echo "  postgres.sql ($(wc -c < "$dir/postgres.sql") bytes)"
    fi
    local pairs; pairs="$(volumes)"
    echo "Volumes to copy:"; volume_sizes $(echo "$pairs" | cut -d" " -f2)
    if [ "$online" = 0 ]; then
        # InfluxDB and PostgreSQL write continuously; a tarball of a live
        # volume can hold a half-written file. Stopping costs a minute of
        # measurements and buys a restore that works. Whatever happens
        # below, the stack comes back: a failed tar must not leave the
        # monitor down.
        trap 'echo "backup interrupted: starting the stack again"; compose up -d' EXIT
        compose down
    fi
    local key name
    while read -r key name; do
        [ -n "$key" ] || continue
        docker run --rm -v "$name:/from:ro" -v "$dir/volumes:/to" alpine:3.20 \
            tar czf "/to/$key.tgz" -C /from .
        echo "volume=$key=$name" >> "$dir/manifest"
        echo "  volumes/$key.tgz ($(du -h "$dir/volumes/$key.tgz" | cut -f1)) from $name"
    done <<< "$pairs"
    if [ "$online" = 0 ]; then
        trap - EXIT
        compose up -d
    fi
    # Secrets and the target list: the env file is what `install` refuses to
    # regenerate, the config directory is the YAML import/export.
    install -m 0600 "$ENV_FILE" "$dir/env"
    if [ -d "$(config_dir)" ]; then
        mkdir -p "$dir/config" && cp -a "$(config_dir)/." "$dir/config/"
    fi
    chmod 0700 "$dir"
    echo "Done: $dir (mode 700; it holds every secret of this install)"
}

cmd_restore() {
    local dir="" force=0 start=1 yes=0
    while [ $# -gt 0 ]; do
        case "$1" in
            --force) force=1; shift ;;
            --no-start) start=0; shift ;;
            --yes|-y) yes=1; shift ;;
            -*) echo "unknown option $1" >&2; exit 2 ;;
            *) dir="$1"; shift ;;
        esac
    done
    [ -n "$dir" ] && [ -f "$dir/manifest" ] || { echo "usage: smoking-pi restore DIR (a directory written by 'smoking-pi backup')" >&2; exit 2; }
    dir="$(cd "$dir" && pwd)"
    local b_edition; b_edition="$(sed -n 's/^edition=//p' "$dir/manifest")"
    # A fresh card (packaged, nothing installed, no edition recorded) is
    # restored as the edition it was backed up from, and records it the way
    # `install` does, so the unit starts that one.
    if [ -n "$b_edition" ] && [ "$EDITION_DEFAULTED" = 1 ] && [ "${SMOKING_PI_PACKAGED:-}" = 1 ] && [ -n "$EDITION_FILE" ] \
        && [ ! -f "$ENV_FILE" ] && [ -d "$SMOKING_PI_HOME/editions/$b_edition" ]; then
        SMOKING_PI_EDITION="$b_edition"; EDITION_DIR="$SMOKING_PI_HOME/editions/$b_edition"
        mkdir -p "$(dirname "$EDITION_FILE")" && printf '%s\n' "$b_edition" > "$EDITION_FILE"
        echo "  no edition recorded here: restoring as the backup's, $b_edition"
    fi
    need_edition
    [ "$b_edition" = "$SMOKING_PI_EDITION" ] || { echo "backup is of the $b_edition edition, this is $SMOKING_PI_EDITION" >&2; exit 1; }
    # The env file first: without it Compose cannot even name the project.
    local token_changed=0
    if [ -f "$ENV_FILE" ] && [ "$force" = 0 ]; then
        echo "keeping the existing $ENV_FILE (--force overwrites it with the backup's)"
    else
        # Whether the MCP token this host had is the one coming back (none
        # before -- a card wiped since -- counts as changed: nothing says
        # what a gateway here was given).
        [ "$(sed -n 's/^MCP_API_TOKEN=//p' "$ENV_FILE" 2>/dev/null | tail -1)" \
            = "$(sed -n 's/^MCP_API_TOKEN=//p' "$dir/env" | tail -1)" ] && [ -f "$ENV_FILE" ] || token_changed=1
        mkdir -p "$(dirname "$ENV_FILE")" && install -m 0600 "$dir/env" "$ENV_FILE"
        echo "  env file restored to $ENV_FILE"
    fi
    if [ -d "$dir/config" ]; then
        if [ -n "$(ls -A "$(config_dir)" 2>/dev/null)" ] && [ "$force" = 0 ]; then
            echo "keeping the existing config directory (--force overwrites it)"
        else
            mkdir -p "$(config_dir)" && cp -a "$dir/config/." "$(config_dir)/"
            echo "  config restored to $(config_dir)"
        fi
    fi
    # A purge --config on a package removes both; Compose would create the
    # output bind source itself, root-owned, rather than leave it to us.
    mkdir -p "$(config_dir)" "$(output_dir)"
    local proj; proj="$(project)"
    # Which tarball goes into which volume is decided by THIS project's
    # rendered config: the backup's key (postgres-data) resolves to the name
    # this stack mounts, whatever the backup's project or edition named it.
    local pairs; pairs="$(volumes)"
    local plan="" skipped="" key name tgz
    for tgz in "$dir"/volumes/*.tgz; do
        [ -e "$tgz" ] || continue
        key="$(basename "$tgz" .tgz)"
        name="$(echo "$pairs" | awk -v k="$key" '$1 == k {print $2}')"
        if [ -n "$name" ]; then plan="$plan$key $name"$'\n'; else skipped="$skipped $key"; fi
    done
    [ -n "$plan" ] || { echo "no tarball in $dir/volumes matches a volume this stack mounts" >&2; exit 1; }
    echo "Volumes to replace (current contents are deleted first):"
    printf '%s' "$plan" | while read -r key name; do echo "  $name  <- volumes/$key.tgz"; done
    [ -z "$skipped" ] || echo "Skipped (no active service mounts them here):$skipped"
    if grep -qx 'online=1' "$dir/manifest"; then
        echo "WARNING: this backup was taken --online; a database file in it may be inconsistent."
    fi
    # As destructive as purge, so the same gate.
    [ "$yes" = 1 ] || confirm_typed "$proj"
    compose down
    local failed=""
    while read -r key name; do
        [ -n "$key" ] || continue
        docker volume create --label "com.docker.compose.project=$proj" \
            --label "com.docker.compose.volume=$key" "$name" >/dev/null
        # Replace the contents, dotfiles included: a merge would leave a
        # newer WAL beside an older data directory. Two steps, reported
        # apart, and a failure moves on to the next volume rather than
        # aborting with the rest untouched and unmentioned.
        if ! docker run --rm -v "$name:/to" alpine:3.20 sh -c 'find /to -mindepth 1 -delete'; then
            echo "  $name: could not empty the volume" >&2; failed="$failed $name"; continue
        fi
        if ! docker run --rm -v "$name:/to" -v "$dir/volumes/$key.tgz:/from.tgz:ro" alpine:3.20 tar xzf /from.tgz -C /to; then
            echo "  $name: emptied but the tarball did not extract" >&2; failed="$failed $name"; continue
        fi
        echo "  volume $name restored"
    done <<< "$plan"
    if [ -n "$failed" ]; then
        echo "restore INCOMPLETE; the stack is stopped. Failed:$failed" >&2
        exit 1
    fi
    # A restore is how a new card becomes this install: like install, it
    # leaves the unit enabled, or the restored stack is gone after the
    # first reboot (a fresh card's unit was never enabled).
    if [ "$start" = 1 ]; then
        compose up -d
        enable_unit
        echo "Restored from $dir. Check: $(cli_name) doctor --live"
    else
        enable_unit --enable-only
        echo "Restored from $dir; the stack is stopped. Start it: $(cli_name) up"
    fi
    # The MCP token came back with the env file. An OpenClaw gateway here
    # registered before this restore still sends the old one, and every tool
    # call is refused (seen on the staging Pi, 2026-10-13). A kept env file,
    # or the same token, changes nothing a gateway holds.
    [ "$token_changed" = 1 ] || return 0
    case ",$(profiles)," in
        *,mcp,*)
            echo "If an OpenClaw gateway on this machine was connected before this restore,"
            echo "connect it again: $(cli_name) openclaw (it still holds the old MCP token)" ;;
    esac
}

cmd_purge() {
    local config=0 yes=0
    while [ $# -gt 0 ]; do
        case "$1" in
            --config) config=1; shift ;;
            --yes|-y) yes=1; shift ;;
            *) echo "unknown option $1" >&2; exit 2 ;;
        esac
    done
    need_edition
    local proj; proj="$(project)"
    [ -n "$proj" ] || { echo "cannot resolve the Compose project (is the env file there?)" >&2; exit 1; }
    local vols; vols="$(volume_names)"
    echo "This deletes the Docker volumes the $proj stack uses:"
    if [ -n "$vols" ]; then volume_sizes $vols; else echo "  (none)"; fi
    if [ "$config" = 1 ]; then echo "and the env file $ENV_FILE, $(config_dir), $(output_dir)."; fi
    echo "The measurements and the target list go with them. 'smoking-pi backup' first if in doubt."
    # --yes is for scripts (the bats suite, an automated teardown); a person
    # types the project name.
    [ "$yes" = 1 ] || confirm_typed "$proj"
    if [ "$config" = 1 ] && [ "${SMOKING_PI_PACKAGED:-0}" = 1 ] && command -v systemctl >/dev/null 2>&1 \
        && systemctl cat smoking-pi >/dev/null 2>&1; then
        # Stopped while its env file is still there (its ExecStop is
        # `smoking-pi down`, which needs it), and disabled: without the env
        # file every boot would fail it. Install enables it again.
        systemctl disable --now smoking-pi >/dev/null 2>&1 || true
    fi
    compose down
    if [ -n "$vols" ]; then
        # Report and carry on to the --config cleanup: a volume still held
        # by a stray container is a message, not a reason to leave the env
        # file behind.
        echo "$vols" | xargs docker volume rm >/dev/null || echo "warning: some volumes could not be removed (docker volume ls)" >&2
    fi
    if [ "$config" = 1 ]; then
        rm -f "$ENV_FILE"
        # The edition install recorded beside it (packaged): install
        # writes it again, and left behind it kept /etc/smoking-pi from
        # ever being removed by apt purge.
        [ -z "$EDITION_FILE" ] || rm -f "$EDITION_FILE"
        rm -rf "$(config_dir)" "$(output_dir)"
        # From a clone the directories are tracked as .gitkeep placeholders.
        # Packaged, they are not put back: an empty /etc/smoking-pi/config
        # looked like a half-removed install, and install (setup.sh) and
        # restore create them again.
        [ "${SMOKING_PI_PACKAGED:-0}" = 1 ] || mkdir -p "$(config_dir)" "$(output_dir)"
    fi
    echo "Purged. 'smoking-pi install' starts over."
}
