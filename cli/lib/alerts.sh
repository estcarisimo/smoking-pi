# shellcheck shell=bash
# smoking-pi alerts: `alerts`, where they go.
# Sourced by cli/smoking-pi, never run on its own.

# The gateway token of the OpenClaw on this machine, from the invoking
# user's openclaw.json (under sudo, the one who ran sudo). Empty if none.
openclaw_config_token() {
    local user="${SUDO_USER:-${USER:-}}" home f
    home="$(getent passwd "$user" 2>/dev/null | cut -d: -f6)"
    f="${home:-$HOME}/.openclaw/openclaw.json"
    [ -r "$f" ] && command -v python3 >/dev/null || return 0
    python3 -c 'import json,sys; print(((json.load(open(sys.argv[1])).get("gateway") or {}).get("auth") or {}).get("token") or "")' "$f" 2>/dev/null || true
}

profiles_with() {
    # COMPOSE_PROFILES with $1 added once.
    local current
    current="$(env_get COMPOSE_PROFILES)"
    case ",$current," in *",$1,"*) echo "$current" ;; *) echo "${current:+$current,}$1" ;; esac
}

alerter_preflight_line() {
    # The alerter checks delivery once at start and logs one line: INFO when
    # it looks usable, ERROR otherwise (notifier.preflight). Only lines since
    # $1, the moment before the recreate: an older container's verdict is
    # about the old settings. Two of the errors do not say "preflight".
    local since="$1" i line
    for i in $(seq 1 30); do
        line="$(compose logs alerter --since "$since" 2>/dev/null \
            | grep -E 'Delivery preflight|NOTIFY_MODE=(openclaw|webhook|telegram) but' | tail -1 || true)"
        [ -z "$line" ] || { echo "$line"; return 0; }
        sleep 2
    done
}

# The weekly summary's day as WEEKLY_DAY takes it (mon..sun), from any
# case or the full name; empty when it is not a day.
alerts_weekday() {
    local d="${1,,}"
    case "$d" in
        mon|monday) echo mon ;; tue|tuesday) echo tue ;; wed|wednesday) echo wed ;;
        thu|thursday) echo thu ;; fri|friday) echo fri ;; sat|saturday) echo sat ;;
        sun|sunday) echo sun ;;
    esac
}

alerts_day_name() {
    case "$1" in
        mon) echo Monday ;; tue) echo Tuesday ;; wed) echo Wednesday ;; thu) echo Thursday ;;
        fri) echo Friday ;; sat) echo Saturday ;; sun) echo Sunday ;; *) echo "$1" ;;
    esac
}

alerts_zone() {
    local zone
    zone="$(env_get DIGEST_TZ)"; zone="${zone:-$(env_get TZ)}"
    echo "${zone:-UTC}"
}

alerts_set_weekly() {
    # $1: mon..sun, or off; $2: HH:MM (both validated by the caller).
    if [ "$1" = off ]; then
        env_set WEEKLY_ENABLED false
        echo "Weekly summary off."
        return 0
    fi
    env_set WEEKLY_ENABLED true
    env_set WEEKLY_DAY "$1"
    env_set WEEKLY_AT "$2"
    echo "Weekly summary on $(alerts_day_name "$1")s at $2 $(alerts_zone): the week before, against the one before it."
    echo "A week with nothing worth telling sends nothing (the alerter logs that it skipped it)."
    case "$(env_get NOTIFY_MODE)" in
        ""|off) echo "Note: NOTIFY_MODE is off, so the summary is logged, not sent. $(cli_name) alerts --telegram (or --openclaw, --webhook) sends it." ;;
    esac
}

alerts_set_zone() {
    env_set DIGEST_TZ "$1"
    echo "Every time a message prints, its schedule and quiet hours are in $1."
}

alerts_status() {
    # What `alerts` has set, read from the env file: where they go, when the
    # weekly goes, quiet hours, the dead man's switch.
    local mode weekly day at zone legacy=""
    mode="$(env_get NOTIFY_MODE)"; zone="$(alerts_zone)"
    case "${mode:-off}" in
        telegram) echo "Delivery:     Telegram chat $(env_get TELEGRAM_CHAT_ID)" ;;
        openclaw) echo "Delivery:     OpenClaw, to $(env_get OPENCLAW_TO)" ;;
        webhook) echo "Delivery:     a webhook" ;;
        *) echo "Delivery:     off (evaluated and logged, not sent)" ;;
    esac
    weekly="$(env_get WEEKLY_ENABLED)"
    day="$(env_get WEEKLY_DAY)"; day="${day:-mon}"
    at="$(env_get WEEKLY_AT)"
    if [ -z "$weekly" ] && [ "$(env_get DIGEST_ENABLED)" = true ]; then
        # The alerter maps the retired daily digest the same way (weekly.py).
        weekly=true; at="${at:-$(env_get DIGEST_AT)}"
        legacy=" (from the retired daily digest's settings; $(cli_name) alerts --weekly sets it)"
    fi
    if [ "$weekly" = true ]; then
        echo "Weekly:       $(alerts_day_name "$day")s at ${at:-08:00} $zone, only when the week is news$legacy"
    else
        echo "Weekly:       off"
    fi
    if [ -n "$(env_get QUIET_FROM)" ] && [ -n "$(env_get QUIET_UNTIL)" ]; then
        echo "Quiet hours:  $(env_get QUIET_FROM) to $(env_get QUIET_UNTIL) $zone"
    else
        echo "Quiet hours:  off"
    fi
    if [ -n "$(env_get ALERT_DEADMAN_URL)" ]; then
        echo "Dead man:     on (the check-in URL is set, hidden)"
    else
        echo "Dead man:     off"
    fi
}

alerts_set_quiet() {
    # $1: HH:MM-HH:MM or off (validated and normalized by the caller).
    if [ "$1" = off ]; then
        env_set QUIET_FROM ""
        env_set QUIET_UNTIL ""
        echo "Quiet hours off: an alert sounds whenever it happens."
        return 0
    fi
    env_set QUIET_FROM "${1%-*}"
    env_set QUIET_UNTIL "${1#*-}"
    local zone
    zone="$(alerts_zone)"
    echo "Quiet hours ${1%-*} to ${1#*-} $zone: nothing sounds, the Internet down included."
    echo "Still going on at ${1#*-}, it is alerted then; over by then, it is in one silent message at ${1#*-}."
}

# HH:MM, or H:MM made HH:MM (as the alerter reads it); empty when neither.
alerts_hhmm() {
    case "$1" in
        [0-9]:[0-5][0-9]) echo "0$1" ;;
        [01][0-9]:[0-5][0-9]|2[0-3]:[0-5][0-9]) echo "$1" ;;
    esac
}

alerts_deadman() {
    # $1: off, or empty to read the URL. The URL is a credential (anyone
    # holding it can check in for this host): typed hidden or read from
    # stdin, never argv, never printed; the alerter logs its host only.
    local url
    if [ "$1" = off ]; then
        env_set ALERT_DEADMAN_URL ""
        echo "Dead man's switch off: nothing outside this host expects its check-ins."
        config_apply ALERT_DEADMAN_URL
        return
    fi
    echo "Every alert comes from this host, so when it hangs, loses power or its"
    echo "card, nothing says so. A check-in service does: the alerter checks in"
    echo "every 5 minutes, and the service tells you when the check-ins stop."
    echo "On healthchecks.io (free; or a Healthchecks of your own): add a check,"
    echo "period 5 minutes, grace 10, and copy its ping URL."
    if [ -t 0 ]; then
        read -r -s -p "Ping URL (hidden): " url || url=""; echo
    else
        IFS= read -r url || true
    fi
    # A pasted URL brings a trailing space or a CR; trim it, then refuse
    # anything the env file or Compose would read as something else.
    url="${url//$'\r'/}"
    url="${url#"${url%%[![:space:]]*}"}"; url="${url%"${url##*[![:space:]]}"}"
    case "$url" in
        *[[:space:]\'\"\$\\\`]*) echo "That is not a ping URL (it has a space, a quote, \$ or \\); nothing changed." >&2; return 2 ;;
        http://?*/*|https://?*/*) ;;
        *) echo "That is not a ping URL (https://..., with a path); nothing changed." >&2; return 2 ;;
    esac
    case "$url" in
        http://*) echo "Note: http, not https -- the URL is the credential, and it travels in the clear." >&2 ;;
    esac
    env_set ALERT_DEADMAN_URL "$url"
    local since line i
    since="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    config_apply ALERT_DEADMAN_URL
    # The proof is the alerter's own first check-in (it checks in at start),
    # not this command: logged with the host, never the URL.
    grep -qx alerter <(compose ps --status running --services 2>/dev/null) || return 0
    for i in $(seq 1 45); do
        line="$(compose logs alerter --since "$since" 2>/dev/null \
            | grep -E "Dead man's switch:" | tail -1 || true)"
        [ -z "$line" ] || break
        sleep 2
    done
    if [ -z "$line" ]; then
        echo "The alerter has not checked in yet; its log says why: $(cli_name) logs alerter" >&2
        return 1
    fi
    echo "${line#*alerter.dead_man: }"
    case "$line" in *"checked in"*) echo "Now pause the check once in the service to see its alert reach you." ;; *) return 1 ;; esac
}

alerts_telegram() {
    # $1: a chat id from --to, or empty; $2: --yes. The token is a secret:
    # typed (hidden) or read from stdin, never argv, never printed. The chat
    # id is found for the owner: they message the bot, and the alerter's own
    # image reads it (telegram.py find-chat), so the token stays off any
    # command line.
    local to="$1" yes="$2" token
    if [ -z "$(env_get TELEGRAM_BOT_TOKEN)" ]; then
        echo "Alerts go to a Telegram bot of your own. To make one: in Telegram, open"
        echo "@BotFather, send /newbot, pick a name, and copy the token it gives you."
        if [ -t 0 ]; then
            read -r -s -p "Bot token (hidden): " token || token=""; echo
        else
            IFS= read -r token || true
        fi
        if ! printf '%s' "$token" | grep -Eq '^[0-9]{5,}:[A-Za-z0-9_-]{30,}$'; then
            echo "That is not a bot token (digits, a colon, then about 35 letters and digits); nothing changed." >&2
            return 2
        fi
        env_set TELEGRAM_BOT_TOKEN "$token"
        echo "Bot token saved."
    fi
    [ -n "$to" ] || to="$(env_get TELEGRAM_CHAT_ID)"
    if [ -z "$to" ]; then
        if [ "$yes" = 1 ] || [ ! -t 0 ]; then
            echo "No chat: --to CHAT_ID, or run this in a terminal to find it by messaging the bot." >&2
            echo "(The bot token is saved; it is not asked for again.)" >&2
            return 2
        fi
        # Finding the chat reads the bot's messages, which only one program
        # may do, and marks them read: a bot OpenClaw (or anything else)
        # already listens on would lose them. Sending is never a conflict.
        local shared=""
        read -r -p "Is this bot already used by OpenClaw or another program? [y/N] " shared || shared=""
        case "$shared" in
            [yY]|[yY][eE][sS])
                echo "Then give the chat instead of reading the bot's messages:" >&2
                echo "  $(cli_name) alerts --telegram --to CHAT_ID" >&2
                echo "The chat id: OpenClaw's OPENCLAW_TO is telegram:CHAT_ID; otherwise forward" >&2
                echo "one of your messages to @userinfobot. The bot token is saved." >&2
                return 2 ;;
        esac
        echo "Starting the alerter's image to read the bot's messages (the first time,"
        echo "it may download it). Then open your bot in Telegram and send it any message"
        echo "(a bot can only write to someone who wrote to it first). Waiting up to two minutes..."
        # The chat id is the last line that is only a number; anything else
        # Compose prints on stdout (a pull, a warning) is not it.
        to="$(compose run --rm --no-deps -T alerter python telegram.py find-chat 120 \
            | grep -E '^-?[0-9]+$' | tail -1)" || to=""
        [ -n "$to" ] || { echo "No chat found; run this again, or give it: --to CHAT_ID" >&2; return 1; }
        local a=""
        read -r -p "Send alerts to that chat? [Y/n] " a || a=n
        case "$a" in n|N|no) echo "Nothing changed. Run this again and message the bot, or give it: --to CHAT_ID" >&2; return 1 ;; esac
    fi
    if ! printf '%s' "$to" | grep -Eq '^(-?[0-9]{1,20}|@[A-Za-z0-9_]{5,32})$'; then
        echo "$to is not a Telegram chat id (digits, -100... for a group, or @channel)." >&2
        return 2
    fi
    env_set TELEGRAM_CHAT_ID "$to"
    env_set NOTIFY_MODE telegram
    echo "Alerts go to Telegram chat $to through your bot."
}

alerts_history() {
    # The notification ledger (alerter/notifications.py), read by the alerter
    # itself: it lives in the alerter's state volume, and the zone the
    # times are shown in is the alerter's (DIGEST_TZ, then TZ).
    local days=7 kind="" json=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --days) days="${2:-}"; shift 2 || { echo "--days needs a number of days" >&2; return 2; } ;;
            --kind) kind="${2:-}"; shift 2 || { echo "--kind needs a kind" >&2; return 2; } ;;
            --json) json=1; shift ;;
            -h|--help) usage; return 0 ;;
            *) echo "unknown option $1" >&2; return 2 ;;
        esac
    done
    case "$days" in
        ""|*[!0-9]*) echo "--days takes a whole number of days, 1 to 30." >&2; return 2 ;;
    esac
    if [ "$days" -lt 1 ] || [ "$days" -gt 30 ]; then
        echo "--days takes 1 to 30: the ledger keeps 30 days." >&2; return 2
    fi
    case "$kind" in
        ""|alert|recovery|superseded|update|outbox_summary|quiet_bundle|weekly|digest|watchdog|monitor|report|episode) ;;
        *) echo "--kind takes alert, recovery, update, monitor, episode, watchdog, outbox_summary, quiet_bundle, weekly, digest (before the weekly) or report." >&2; return 2 ;;
    esac
    need_edition
    if ! grep -q '^  alerter:' "$EDITION_DIR/docker-compose.yml"; then
        echo "Alerts are a Pro service; the $SMOKING_PI_EDITION edition does not ship the alerter." >&2
        return 1
    fi
    if ! grep -qx alerter <(compose ps --status running --services 2>/dev/null); then
        echo "The alerter is not running, so it has no ledger to read. Turn it on: $(cli_name) alerts" >&2
        return 1
    fi
    local args=(--days "$days")
    [ -z "$kind" ] || args+=(--kind "$kind")
    [ -z "$json" ] || args+=(--json)
    compose exec -T alerter python notifications.py show "${args[@]}"
}

cmd_alerts() {
    if [ "${1:-}" = history ]; then
        shift; alerts_history "$@"; return
    fi
    local mode="" to="" url="" test_msg="" yes=0 week_day="" week_at="" digest_tz="" quiet="" deadman=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --weekly) if [ "${2:-}" = off ]; then echo "To turn it off: --no-weekly" >&2; return 2; fi
                      if [ -z "${2:-}" ] || [ -z "${3:-}" ] || [ "${2#-}" != "$2" ] || [ "${3#-}" != "$3" ]; then
                          echo "--weekly needs a day and a time: --weekly mon 08:00" >&2; return 2
                      fi
                      week_day="$2"; week_at="$3"; shift 3 ;;
            --no-weekly) week_day=off; shift ;;
            --digest) echo "The daily digest is retired: a weekly summary replaces it, sent only when the week is news." >&2
                      echo "  $(cli_name) alerts --weekly mon 08:00   (and --digest-tz ZONE for the zone)" >&2
                      return 2 ;;
            --digest-tz) case "${2:-}" in ""|-*) echo "--digest-tz needs a zone (Europe/London)" >&2; return 2 ;; esac
                         digest_tz="$2"; shift 2 ;;
            status|--status) [ $# -eq 1 ] && [ -z "$mode$week_day$digest_tz$quiet$to$test_msg$deadman" ] \
                                 || { echo "alerts status goes alone" >&2; return 2; }
                             alerts_status; return 0 ;;
            --quiet) case "${2:-}" in ""|-*) echo "--quiet needs HH:MM-HH:MM or off" >&2; return 2 ;; esac
                     quiet="$2"; shift 2 ;;
            --deadman) if [ "${2:-}" = off ]; then deadman=off; shift 2; else deadman=set; shift; fi ;;
            --telegram) mode=telegram; shift ;;
            --openclaw) mode=openclaw; shift ;;
            --webhook) mode=webhook; shift ;;
            --off) mode=off; shift ;;
            --to) to="${2:-}"; shift 2 || { echo "--to needs a recipient" >&2; return 2; } ;;
            --test) test_msg=1; shift ;;
            --yes|-y) yes=1; shift ;;
            -h|--help) usage; return 0 ;;
            *) echo "unknown option $1" >&2; return 2 ;;
        esac
    done
    need_edition
    # Defined by the edition, whatever the profiles: has_service asks
    # Compose, which lists only enabled services, and a Pro install with
    # the alerts profile off is exactly who this command is for.
    if ! grep -q '^  alerter:' "$EDITION_DIR/docker-compose.yml"; then
        echo "Alerts are a Pro service; the $SMOKING_PI_EDITION edition does not ship the alerter." >&2
        return 1
    fi
    if [ -n "$deadman" ]; then
        # Its own step: it reads a credential from stdin, which a delivery
        # mode may want too.
        [ -z "$mode$week_day$digest_tz$quiet$to$test_msg" ] || { echo "--deadman goes alone" >&2; return 2; }
        [ "$deadman" = off ] && { alerts_deadman off; return; }
        alerts_deadman ""; return
    fi
    # Checked before anything is written, so a typo changes nothing.
    if [ -n "$week_day" ] && [ "$week_day" != off ]; then
        local day; day="$(alerts_weekday "$week_day")"
        [ -n "$day" ] || { echo "--weekly takes a day, mon to sun (or Monday...), then HH:MM: --weekly mon 08:00" >&2; return 2; }
        week_day="$day"
        local hhmm; hhmm="$(alerts_hhmm "$week_at")"
        [ -n "$hhmm" ] || { echo "--weekly takes HH:MM (24-hour, 08:00) after the day; the alerter disables a summary it cannot read." >&2; return 2; }
        week_at="$hhmm"
    fi
    case "$digest_tz" in
        *..*|/*|*[!A-Za-z0-9_+/-]*) echo "$digest_tz is not a zone name (Europe/London)." >&2; return 2 ;;
    esac
    if [ -n "$digest_tz" ] && [ ! -f "/usr/share/zoneinfo/$digest_tz" ]; then
        echo "$digest_tz is not a time zone here (/usr/share/zoneinfo); for example Europe/London." >&2; return 2
    fi
    case "$quiet" in
        ""|off) ;;
        *-*)
            local q_from q_until
            q_from="$(alerts_hhmm "${quiet%-*}")"; q_until="$(alerts_hhmm "${quiet#*-}")"
            if [ -z "$q_from" ] || [ -z "$q_until" ] || [ "$q_from" = "$q_until" ]; then
                echo "--quiet takes two different 24-hour times, HH:MM-HH:MM (23:00-07:00), or off." >&2; return 2
            fi
            quiet="$q_from-$q_until" ;;
        *) echo "--quiet takes two different 24-hour times, HH:MM-HH:MM (23:00-07:00), or off." >&2; return 2 ;;
    esac
    if { [ -n "$week_day" ] || [ -n "$quiet" ] || [ -n "$digest_tz" ]; } && [ -z "$mode" ]; then
        # Only the schedule: nothing about where alerts go changes.
        [ -z "$digest_tz" ] || alerts_set_zone "$digest_tz"
        [ -z "$week_day" ] || alerts_set_weekly "$week_day" "$week_at"
        [ -z "$quiet" ] || alerts_set_quiet "$quiet"
        config_apply WEEKLY_AT
        return 0
    fi
    if [ -z "$mode" ]; then
        [ "$yes" = 0 ] && [ -t 0 ] || { echo "Say where: --telegram, --openclaw, --webhook or --off." >&2; return 2; }
        echo "Where should alerts go?"
        echo "  1) Telegram, directly: a bot of your own (no OpenClaw needed)"
        echo "  2) OpenClaw (Telegram, or whatever channel your gateway has)"
        echo "  3) A webhook"
        echo "  4) Nowhere: evaluate and log only"
        local choice; read -r -p "Choice [1]: " choice
        case "${choice:-1}" in 1) mode=telegram ;; 2) mode=openclaw ;; 3) mode=webhook ;; 4) mode=off ;; *) echo "no such choice" >&2; return 2 ;; esac
    fi

    case "$mode" in
        telegram)
            alerts_telegram "$to" "$yes" || return $?
            to="$(env_get TELEGRAM_CHAT_ID)"
            ;;
        openclaw)
            if [ -z "$(env_get OPENCLAW_GATEWAY_TOKEN)" ]; then
                local token; token="$(openclaw_config_token)"
                if [ -n "$token" ] && { [ "$yes" = 1 ] || { read -r -p "Use the gateway token from your openclaw.json? [Y/n] " a; [ "${a:-y}" != n ] && [ "${a:-y}" != N ]; }; }; then
                    env_set OPENCLAW_GATEWAY_TOKEN "$token"
                    echo "Gateway token: taken from openclaw.json."
                elif [ "$yes" = 0 ] && [ -t 0 ]; then
                    read -r -s -p "Gateway token (gateway.auth.token in ~/.openclaw/openclaw.json): " token; echo
                    [ -n "$token" ] || { echo "no token; nothing changed" >&2; return 1; }
                    env_set OPENCLAW_GATEWAY_TOKEN "$token"
                else
                    echo "No gateway token found. Set it first: smoking-pi config set OPENCLAW_GATEWAY_TOKEN" >&2
                    return 1
                fi
            fi
            [ -n "$to" ] || to="$(env_get OPENCLAW_TO)"
            if [ -z "$to" ]; then
                if [ "$yes" = 0 ] && [ -t 0 ]; then
                    echo "The recipient, in OpenClaw's form (telegram:123456789)."
                    echo "Find it with: openclaw gateway call sessions-list  (deliveryContext.to)"
                    read -r -p "Recipient: " to
                fi
                [ -n "$to" ] || { echo "No recipient: --to telegram:<chat id>" >&2; return 2; }
            fi
            case "$to" in
                *:*) ;;
                *) echo "$to is not in OpenClaw's channel:id form (a bare chat id is not delivered)." >&2; return 2 ;;
            esac
            env_set OPENCLAW_TO "$to"
            env_set OPENCLAW_CHANNEL "${to%%:*}"
            env_set NOTIFY_MODE openclaw
            echo "Alerts go to $to through OpenClaw."
            ;;
        webhook)
            # The URL is often the credential itself (Slack, Discord, ntfy):
            # asked for, or read from stdin, never taken from argv or printed.
            if [ -t 0 ]; then
                read -r -s -p "Webhook URL: " url; echo
            else
                IFS= read -r url || true
            fi
            case "$url" in
                http://*|https://*) ;;
                *) echo "The webhook needs an http(s) URL (typed at the prompt, or on stdin)." >&2; return 2 ;;
            esac
            case "$url" in
                *"'"*) echo "a URL cannot contain ' in the env file" >&2; return 2 ;;
            esac
            env_set ALERT_WEBHOOK_URL "$url"
            env_set NOTIFY_MODE webhook
            echo "Alerts go to the webhook. A bearer token, if it needs one: smoking-pi config set ALERT_WEBHOOK_TOKEN"
            ;;
        off)
            env_set NOTIFY_MODE off
            echo "Alerts are evaluated and logged, not delivered."
            ;;
    esac

    [ -z "$digest_tz" ] || alerts_set_zone "$digest_tz"
    [ -z "$week_day" ] || alerts_set_weekly "$week_day" "$week_at"
    [ -z "$quiet" ] || alerts_set_quiet "$quiet"
    env_set COMPOSE_PROFILES "$(profiles_with alerts)"
    # The mcp-server delivers get_chart through the same OpenClaw keys.
    local services="alerter"
    has_service mcp-server && services="alerter mcp-server"
    local started
    started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    # shellcheck disable=SC2086  # one word per service
    compose up -d $services
    [ "$mode" = off ] && return 0

    local line; line="$(alerter_preflight_line "$started")"
    echo "${line:-The alerter logged no delivery preflight; see: smoking-pi logs alerter}"
    case "$line" in
        *" INFO "*"Delivery preflight"*) ;;
        *) echo "Delivery is not working yet; the line above says why (docs/alerting.md)." >&2; return 1 ;;
    esac
    if [ -z "$test_msg" ] && [ "$yes" = 0 ] && [ -t 0 ]; then
        read -r -p "Send a test message now? [y/N] " a
        case "$a" in y|Y|yes) test_msg=1 ;; esac
    fi
    if [ -n "$test_msg" ]; then
        if compose exec -T alerter python main.py --test >/dev/null 2>&1; then
            if [ "$mode" = webhook ]; then echo "Test message sent to the webhook."
            elif [ "$mode" = telegram ]; then echo "Test message sent: it should be in Telegram chat $to now."
            else echo "Test message sent: it should be on its way to $to."; fi
        else
            echo "The test message was NOT delivered: smoking-pi logs alerter" >&2
            return 1
        fi
    fi
}
