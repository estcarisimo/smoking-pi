# shellcheck shell=bash
# smoking-pi watchdog: put the host right when it stops measuring by itself.
# Sourced by cli/smoking-pi, never run on its own.
#
# Two failures, both seen on the reference Pi, that nothing recovered from
# without a person:
#
#  * The Wi-Fi radio hangs. wlan0 stays "up", often still associated, and
#    receives nothing: every target at 100% loss for hours (docs/wifi.md,
#    "When the radio hangs" -- 21 h, 3 h 20 min, 17 h on the staging Pi).
#    A power cycle ended each one. The check pings the router and two
#    public addresses through the Wi-Fi interface; when none answers for
#    WATCHDOG_WIFI_MIN minutes, it reloads the driver; still nothing
#    WATCHDOG_REBOOT_MIN minutes later, it
#    reboots -- at most once every WATCHDOG_REBOOT_HOURS, so a router that
#    is really off for a day costs a few reboots, not a loop. A reboot
#    loses nothing: with the uplink gone there is nothing to measure.
#  * SmokePing does not start. After a power cycle the container ran but
#    its s6 service stayed "down (not started yet)" and nothing was
#    measured for 16 minutes, until a manual restart. The check restarts
#    the container when svc-smokeping is not up WATCHDOG_SMOKEPING_MIN
#    minutes after the container started, at most three times in six hours.
#
# `watchdog enable` installs a systemd timer that runs `watchdog check`
# every minute, as root. Every action is a line in the unit's journal and in
# $WATCHDOG_STATE/events.log, which `watchdog` (status) shows.
#
# The SMOKING_PI_WATCHDOG_* variables below exist for the tests (and for a
# rehearsal with short delays); they are not settings.

WATCHDOG_STATE="${SMOKING_PI_WATCHDOG_STATE:-/var/lib/smoking-pi/watchdog}"
WATCHDOG_UNIT_DIR="${SMOKING_PI_WATCHDOG_UNIT_DIR:-/etc/systemd/system}"
WATCHDOG_SYS_NET="${SMOKING_PI_WATCHDOG_SYS_NET:-/sys/class/net}"
WATCHDOG_SYS_MODULE="${SMOKING_PI_WATCHDOG_SYS_MODULE:-/sys/module}"
WATCHDOG_BOOT_ID="${SMOKING_PI_WATCHDOG_BOOT_ID:-/proc/sys/kernel/random/boot_id}"
WATCHDOG_WIFI_MIN="${SMOKING_PI_WATCHDOG_WIFI_MIN:-10}"
WATCHDOG_REBOOT_MIN="${SMOKING_PI_WATCHDOG_REBOOT_MIN:-5}"
WATCHDOG_REBOOT_HOURS="${SMOKING_PI_WATCHDOG_REBOOT_HOURS:-6}"
WATCHDOG_SMOKEPING_MIN="${SMOKING_PI_WATCHDOG_SMOKEPING_MIN:-5}"
WATCHDOG_SMOKEPING_MAX=3        # restarts in any six hours, then it only logs
# Beyond the router: a router that does not answer ping itself (some
# don't), or a rule that blocks it, must not read as a dead radio while
# the Internet answers through it.
WATCHDOG_INTERNET="${SMOKING_PI_WATCHDOG_INTERNET:-1.1.1.1 8.8.8.8}"

wd_now() { echo "${SMOKING_PI_WATCHDOG_NOW:-$(date +%s)}"; }

wd_get() {
    # One key from the state file; empty when absent.
    [ -r "$WATCHDOG_STATE/state" ] && sed -n "s/^$1=//p" "$WATCHDOG_STATE/state" | tail -1 || true
}

wd_set() {
    # wd_set KEY VALUE [KEY VALUE...]; an empty VALUE removes the key.
    # Written whole and renamed, so a reboot in the middle leaves the old
    # file, never half of one.
    local tmp k v
    mkdir -p "$WATCHDOG_STATE"
    tmp="$(mktemp "$WATCHDOG_STATE/.state.XXXXXX")"
    chmod 0644 "$tmp"    # `watchdog` (status) reads it without sudo
    [ -f "$WATCHDOG_STATE/state" ] && cp "$WATCHDOG_STATE/state" "$tmp"
    while [ $# -ge 2 ]; do
        k="$1"; v="$2"; shift 2
        sed -i "/^$k=/d" "$tmp"
        [ -z "$v" ] || echo "$k=$v" >> "$tmp"
    done
    mv -f "$tmp" "$WATCHDOG_STATE/state"
}

wd_log() {
    # An action or a finding: to stdout (the unit's journal) and to the
    # event log `watchdog` shows, which keeps its last 200 lines.
    local line
    line="$(date -u -d "@$(wd_now)" '+%Y-%m-%dT%H:%M:%SZ') $*"
    echo "$*"
    mkdir -p "$WATCHDOG_STATE"
    echo "$line" >> "$WATCHDOG_STATE/events.log"
    if [ "$(wc -l < "$WATCHDOG_STATE/events.log")" -gt 200 ]; then
        tail -n 200 "$WATCHDOG_STATE/events.log" > "$WATCHDOG_STATE/events.log.tmp"
        mv -f "$WATCHDOG_STATE/events.log.tmp" "$WATCHDOG_STATE/events.log"
    fi
}

wd_minutes() { echo $(( ($1 + 30) / 60 )); }

# --- Wi-Fi ---------------------------------------------------------------

wd_wireless() { [ -d "$WATCHDOG_SYS_NET/$1/wireless" ] || [ -L "$WATCHDOG_SYS_NET/$1/phy80211" ]; }

wd_rx_packets() { cat "$WATCHDOG_SYS_NET/$1/statistics/rx_packets" 2>/dev/null || echo 0; }

wd_received() {
    # "N packets received" since the count $2 on interface $1: a deaf radio
    # receives nothing, an AP that is gone mostly nothing. A driver reload
    # makes a new interface, whose counters start again at zero.
    local base rx
    rx="$(wd_rx_packets "$1")"
    base="${2:-$rx}"
    if [ "$rx" -ge "$base" ]; then echo "$((rx - base)) packets received"; else echo "packet count reset"; fi
}

wd_driver_module() {
    # The kernel module behind the interface (brcmfmac on a Pi); empty when
    # the driver is built in, and then there is nothing to reload.
    local m
    m="$(readlink -f "$WATCHDOG_SYS_NET/$1/device/driver/module" 2>/dev/null)" || return 0
    [ -n "$m" ] && [ -d "$m" ] && basename "$m"
    return 0
}

wd_reload_driver() {
    # Remove the module with the ones that hold it (brcmfmac_cyw or
    # brcmfmac_wcc on a Pi: vendor modules brcmfmac loads), load it again,
    # and the holders after it. NetworkManager reconnects by itself. A
    # wedged driver can hang modprobe -r, hence the timeouts.
    local mod="$1" holders=() h
    for h in "$WATCHDOG_SYS_MODULE/$mod/holders/"*; do
        [ -e "$h" ] && holders+=("$(basename "$h")")
    done
    timeout 60 modprobe -r "${holders[@]}" "$mod" || return 1
    timeout 60 modprobe "$mod" || return 1
    for h in "${holders[@]}"; do timeout 60 modprobe "$h" || true; done
}

wd_reaches() {
    # Whether anything answers through $1: the router $2 (when known), else
    # an address beyond it. One reply from any of them is enough.
    local dev="$1" gw="$2" a
    if [ -n "$gw" ] && ping -n -q -c 3 -W 2 -I "$dev" "$gw" >/dev/null 2>&1; then return 0; fi
    for a in $WATCHDOG_INTERNET; do
        ping -n -q -c 2 -W 2 -I "$dev" "$a" >/dev/null 2>&1 && return 0
    done
    return 1
}

wd_link_state() {
    # "associated to <bssid>" or "not associated", for the log only.
    local out
    out="$(iw dev "$1" link 2>/dev/null | head -1)" || out=""
    case "$out" in
        Connected\ to*) echo "associated to $(echo "$out" | awk '{print $3}')" ;;
        *) echo "not associated" ;;
    esac
}

wd_wifi() {
    # One look at the uplink. Healthy (the router or the Internet answers
    # through it): remember the interface and router, end any episode.
    # Nothing answers: count from the first failed look, and act at the
    # thresholds -- on an interface this check has seen healthy, so a host
    # whose Wi-Fi never worked here is not rebooted over it.
    local reboot="$1" dry="$2"
    local now route dev="" gw="" since reload_at rebooted rx0 elapsed
    now="$(wd_now)"
    route="$(ip -4 route show default 2>/dev/null | head -1)" || route=""
    if [ -n "$route" ]; then
        dev="$(echo "$route" | sed -n 's/.* dev \([^ ]*\).*/\1/p')"
        gw="$(echo "$route" | sed -n 's/.* via \([^ ]*\).*/\1/p')"
        if [ -z "$dev" ] || ! wd_wireless "$dev"; then
            # Wired (or a tunnel): not this check's business. A Wi-Fi
            # episode in progress ended when the route moved.
            [ -z "$(wd_get wifi_since)" ] || wd_log "wifi: the uplink is now ${dev:-unknown}, not Wi-Fi; nothing to watch"
            wd_set wifi_since "" wifi_reload_at "" wifi_rx0 "" wifi_dev "" wifi_gw ""
            return 0
        fi
    else
        # No default route at all: a hung radio can lose its association
        # and with it the DHCP lease. Only a host this check has seen
        # healthy on Wi-Fi counts that as the radio; a wired host with its
        # cable out, or Wi-Fi never set up, is left alone.
        dev="$(wd_get wifi_dev)"; gw="$(wd_get wifi_gw)"
        [ -n "$dev" ] && wd_wireless "$dev" || return 0
    fi
    if wd_reaches "$dev" "$gw"; then
        since="$(wd_get wifi_since)"
        if [ -n "$since" ]; then
            wd_log "wifi: $dev reaches the network again after $(wd_minutes $((now - since))) min"
        fi
        wd_set wifi_dev "$dev" wifi_gw "$gw" wifi_since "" wifi_reload_at "" wifi_rx0 ""
        return 0
    fi
    [ "$(wd_get wifi_dev)" = "$dev" ] || return 0
    since="$(wd_get wifi_since)"
    if [ -z "$since" ] || [ "$(wd_get wifi_boot)" != "$(cat "$WATCHDOG_BOOT_ID" 2>/dev/null)" ]; then
        # A new episode, or the first look after a boot: the clock starts
        # now, so a reboot is followed by a full WATCHDOG_WIFI_MIN again.
        wd_log "wifi: nothing answers on $dev, neither router ${gw:-unknown} nor the Internet ($(wd_link_state "$dev")); watching"
        wd_set wifi_since "$now" wifi_reload_at "" wifi_rx0 "$(wd_rx_packets "$dev")" wifi_boot "$(cat "$WATCHDOG_BOOT_ID" 2>/dev/null)"
        return 0
    fi
    elapsed=$((now - since))
    rx0="$(wd_get wifi_rx0)"
    reload_at="$(wd_get wifi_reload_at)"
    if [ -z "$reload_at" ]; then
        [ "$elapsed" -ge $((WATCHDOG_WIFI_MIN * 60)) ] || return 0
        local mod what
        mod="$(wd_driver_module "$dev")"
        what="nothing answers on $dev for $(wd_minutes "$elapsed") min ($(wd_link_state "$dev"), $(wd_received "$dev" "$rx0") meanwhile)"
        if [ -z "$mod" ]; then
            wd_log "wifi: $what; the driver is built in, nothing to reload"
        elif [ "$dry" = 1 ]; then
            wd_log "wifi: $what; would reload the $mod driver (dry run)"
        else
            wd_log "wifi: $what; reloading the $mod driver"
            wd_reload_driver "$mod" || wd_log "wifi: reloading $mod failed"
        fi
        # Counted again from here: the reload restarted the counters.
        wd_set wifi_reload_at "$now" wifi_rx0 "$(wd_rx_packets "$dev")"
        return 0
    fi
    [ $((now - reload_at)) -ge $((WATCHDOG_REBOOT_MIN * 60)) ] || return 0
    local what="still nothing answers on $dev $(wd_minutes $((now - reload_at))) min after the driver reload ($(wd_link_state "$dev"), $(wd_received "$dev" "$rx0") since); $(wd_minutes "$elapsed") min in all"
    if [ "$reboot" = 0 ]; then
        # Said once per episode, not every minute.
        [ "$(wd_get wifi_told)" = "$since" ] && return 0
        wd_log "wifi: $what; not rebooting (installed with --no-reboot)"
        wd_set wifi_told "$since"
        return 0
    fi
    rebooted="$(wd_get last_reboot)"
    if [ -n "$rebooted" ] && [ $((now - rebooted)) -lt $((WATCHDOG_REBOOT_HOURS * 3600)) ]; then
        [ "$(wd_get wifi_told)" = "$since" ] && return 0
        wd_log "wifi: $what; not rebooting: this watchdog rebooted $(wd_minutes $((now - rebooted))) min ago, at most once every $WATCHDOG_REBOOT_HOURS h"
        wd_set wifi_told "$since"
        return 0
    fi
    if [ "$dry" = 1 ]; then
        wd_log "wifi: $what; would reboot (dry run)"
        wd_set wifi_since ""
        return 0
    fi
    wd_log "wifi: $what; rebooting"
    wd_set last_reboot "$now" wifi_since "" wifi_reload_at "" wifi_rx0 ""
    systemctl reboot
}

# --- SmokePing -----------------------------------------------------------

wd_smokeping() {
    local dry="$1" now id started up since restarts recent=() r
    now="$(wd_now)"
    # This stack's container, by its labels: the edition directory is the
    # Compose working directory, whatever the project is called.
    id="$(docker ps --filter "label=com.docker.compose.project.working_dir=$EDITION_DIR" \
            --filter label=com.docker.compose.service=smokeping --format '{{.ID}}' 2>/dev/null | head -1)" || id=""
    [ -n "$id" ] || return 0     # stopped on purpose, or the stack is down
    started="$(docker inspect -f '{{.State.StartedAt}}' "$id" 2>/dev/null)" || return 0
    started="$(date -d "$started" +%s 2>/dev/null)" || return 0
    since=$((now - started))
    [ "$since" -ge $((WATCHDOG_SMOKEPING_MIN * 60)) ] || return 0
    # "true"/"false"; a service whose supervisor never started (s6-rc
    # stuck before it) is as down as one that is supervised but down.
    up="$(docker exec "$id" s6-svstat -u /run/service/svc-smokeping 2>&1)" || true
    case "$up" in
        true) return 0 ;;
        false|*"supervise not running"*) ;;
        *)
            # No s6-svstat, or no such service: an image this check does not
            # know. Said once per container start.
            [ "$(wd_get smokeping_unknown)" = "$started" ] && return 0
            wd_log "smokeping: cannot read svc-smokeping's state in container $id; not checked"
            wd_set smokeping_unknown "$started"
            return 0 ;;
    esac
    for r in $(wd_get smokeping_restarts | tr ',' ' '); do
        [ $((now - r)) -lt 21600 ] && recent+=("$r")
    done
    if [ "${#recent[@]}" -ge "$WATCHDOG_SMOKEPING_MAX" ]; then
        [ "$(wd_get smokeping_told)" = "$started" ] && return 0
        wd_log "smokeping: svc-smokeping is not running $(wd_minutes "$since") min after the container started; not restarting, already restarted ${#recent[@]} times in 6 h ($(cli_name) logs smokeping)"
        wd_set smokeping_told "$started"
        return 0
    fi
    if [ "$dry" = 1 ]; then
        wd_log "smokeping: svc-smokeping is not running $(wd_minutes "$since") min after the container started; would restart it (dry run)"
        return 0
    fi
    wd_log "smokeping: svc-smokeping is not running $(wd_minutes "$since") min after the container started; restarting it"
    recent+=("$now")
    wd_set smokeping_restarts "$(IFS=,; echo "${recent[*]}")"
    cmd_restart smokeping >/dev/null || wd_log "smokeping: the restart failed"
}

# --- The command ---------------------------------------------------------

wd_unit_name() { echo smoking-pi-watchdog; }

wd_write_units() {
    local reboot="$1" cli="$CLI_DIR/smoking-pi" opt="" env=""
    [ "$reboot" = 1 ] || opt=" --no-reboot"
    # Packaged, the edition is the one `install` recorded, and the unit
    # must not pin another; a clone has no record, so it is written here.
    if [ "${SMOKING_PI_PACKAGED:-0}" = 1 ]; then
        cli=/usr/bin/smoking-pi
    else
        env="
Environment=SMOKING_PI_EDITION=$SMOKING_PI_EDITION"
    fi
    cat > "$WATCHDOG_UNIT_DIR/smoking-pi-watchdog.service" <<UNIT
# Written by 'smoking-pi watchdog enable'; removed by 'smoking-pi watchdog disable'.
[Unit]
Description=Smoking Pi watchdog: a hung Wi-Fi radio, a SmokePing that did not start
After=docker.service network.target

[Service]
Type=oneshot
Environment=SMOKING_PI_HOME=$SMOKING_PI_HOME$env
EnvironmentFile=-/etc/default/smoking-pi
ExecStart=$cli watchdog check$opt
UNIT
    cat > "$WATCHDOG_UNIT_DIR/smoking-pi-watchdog.timer" <<'UNIT'
# Written by 'smoking-pi watchdog enable'; removed by 'smoking-pi watchdog disable'.
[Unit]
Description=Run the Smoking Pi watchdog every minute

[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
AccuracySec=10s

[Install]
WantedBy=timers.target
UNIT
}

wd_need_root() {
    [ "$(id -u)" = 0 ] && return 0
    echo "The watchdog runs as root (it reloads drivers and may reboot): run it with sudo." >&2
    return 1
}

wd_status() {
    local enabled=no opts
    if [ -f "$WATCHDOG_UNIT_DIR/smoking-pi-watchdog.timer" ]; then
        if systemctl is-active --quiet smoking-pi-watchdog.timer 2>/dev/null; then enabled=yes; else enabled="installed, not running"; fi
    fi
    echo "Watchdog: $enabled"
    if [ "$enabled" != no ]; then
        opts="$(sed -n 's/^ExecStart=.* watchdog check//p' "$WATCHDOG_UNIT_DIR/smoking-pi-watchdog.service" 2>/dev/null)"
        case "$opts" in
            *--no-reboot*) echo "  Wi-Fi: reload the driver after $WATCHDOG_WIFI_MIN min with nothing answering; never reboot" ;;
            *) echo "  Wi-Fi: reload the driver after $WATCHDOG_WIFI_MIN min with nothing answering, reboot $WATCHDOG_REBOOT_MIN min later (at most every $WATCHDOG_REBOOT_HOURS h)" ;;
        esac
        echo "  SmokePing: restart it when not running $WATCHDOG_SMOKEPING_MIN min after its container started (at most $WATCHDOG_SMOKEPING_MAX times in 6 h)"
    else
        echo "  Turn it on: $(cli_name) watchdog enable"
    fi
    local since; since="$(wd_get wifi_since)"
    [ -z "$since" ] || echo "  Now: nothing answers on $(wd_get wifi_dev) for $(wd_minutes $(( $(wd_now) - since ))) min"
    if [ -s "$WATCHDOG_STATE/events.log" ]; then
        echo
        echo "Last events (UTC):"
        tail -n 10 "$WATCHDOG_STATE/events.log" | sed 's/^/  /'
    fi
}

cmd_watchdog() {
    local sub="${1:-status}" reboot=1 dry=0 a
    [ $# -eq 0 ] || shift
    for a in "$@"; do
        case "$a" in
            --no-reboot) reboot=0 ;;
            --dry-run) dry=1 ;;
            *) echo "unknown option $a (watchdog [status | enable [--no-reboot] | disable | check [--no-reboot] [--dry-run]])" >&2; return 2 ;;
        esac
    done
    case "$sub" in
        status) wd_status ;;
        enable)
            wd_need_root || return 1
            wd_write_units "$reboot"
            systemctl daemon-reload
            systemctl enable --now smoking-pi-watchdog.timer >/dev/null 2>&1 \
                || { echo "systemd did not start smoking-pi-watchdog.timer (systemctl status smoking-pi-watchdog.timer)" >&2; return 1; }
            wd_log "watchdog enabled$([ "$reboot" = 1 ] || echo ' (no reboot)')" >/dev/null
            wd_status ;;
        disable)
            wd_need_root || return 1
            systemctl disable --now smoking-pi-watchdog.timer >/dev/null 2>&1 || true
            rm -f "$WATCHDOG_UNIT_DIR/smoking-pi-watchdog.timer" "$WATCHDOG_UNIT_DIR/smoking-pi-watchdog.service"
            systemctl daemon-reload || true
            wd_log "watchdog disabled" >/dev/null
            echo "Watchdog: off (its event log stays in $WATCHDOG_STATE)" ;;
        check)
            wd_need_root || return 1
            # Each half on its own: a failure in one must not skip the other.
            wd_wifi "$reboot" "$dry" || echo "wifi check failed" >&2
            wd_smokeping "$dry" || echo "smokeping check failed" >&2 ;;
        *) echo "unknown watchdog command: $sub" >&2; return 2 ;;
    esac
}
