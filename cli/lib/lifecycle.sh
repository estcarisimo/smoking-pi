# shellcheck shell=bash
# smoking-pi lifecycle: `upgrade`, `backup`, `restore`, `purge`.
# Sourced by cli/smoking-pi, never run on its own.

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
    # Only the services whose image or config changed are recreated.
    compose up -d --remove-orphans
    remove_disabled
    publish_avahi
    # A clone installed before setup.sh linked the command gets it here.
    link_cli --quiet
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
    if has_service postgres && compose ps --status running --services 2>/dev/null | grep -qx postgres; then
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
    if [ -f "$ENV_FILE" ] && [ "$force" = 0 ]; then
        echo "keeping the existing $ENV_FILE (--force overwrites it with the backup's)"
    else
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
        mkdir -p "$(config_dir)" "$(output_dir)"
    fi
    echo "Purged. 'smoking-pi install' starts over."
}
