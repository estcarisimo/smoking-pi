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

alerts_set_digest() {
    # $1: HH:MM or off (validated by the caller); $2: a zone or empty.
    if [ "$1" = off ]; then
        env_set DIGEST_ENABLED false
        echo "Daily digest off."
        return 0
    fi
    env_set DIGEST_ENABLED true
    env_set DIGEST_AT "$1"
    [ -z "$2" ] || env_set DIGEST_TZ "$2"
    local zone
    zone="$(env_get DIGEST_TZ)"; zone="${zone:-$(env_get TZ)}"
    echo "Daily digest at $1 ${zone:-UTC}: a summary of the last day, so a quiet day reads as \"nothing broke\"."
    case "$(env_get NOTIFY_MODE)" in
        ""|off) echo "Note: NOTIFY_MODE is off, so the digest is logged, not sent. $(cli_name) alerts --telegram (or --openclaw, --webhook) sends it." ;;
    esac
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

cmd_alerts() {
    local mode="" to="" url="" test_msg="" yes=0 digest="" digest_tz=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --digest) case "${2:-}" in ""|-*) echo "--digest needs HH:MM or off" >&2; return 2 ;; esac
                      digest="$2"; shift 2 ;;
            --digest-tz) case "${2:-}" in ""|-*) echo "--digest-tz needs a zone (Europe/London)" >&2; return 2 ;; esac
                         digest_tz="$2"; shift 2 ;;
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
    if [ -n "$digest_tz" ] && [ -z "$digest" ]; then
        echo "--digest-tz goes with --digest HH:MM" >&2; return 2
    fi
    # Checked before anything is written, so a typo changes nothing.
    case "$digest" in
        ""|off) ;;
        [0-9]:[0-5][0-9]) digest="0$digest" ;;  # 7:45, as the alerter reads it
        [01][0-9]:[0-5][0-9]|2[0-3]:[0-5][0-9]) ;;
        *) echo "--digest takes HH:MM (24-hour, 07:45) or off; the alerter disables a digest it cannot read." >&2; return 2 ;;
    esac
    case "$digest_tz" in
        *..*|/*|*[!A-Za-z0-9_+/-]*) echo "$digest_tz is not a zone name (Europe/London)." >&2; return 2 ;;
    esac
    if [ -n "$digest_tz" ] && [ ! -f "/usr/share/zoneinfo/$digest_tz" ]; then
        echo "$digest_tz is not a time zone here (/usr/share/zoneinfo); for example Europe/London." >&2; return 2
    fi
    if [ -n "$digest" ] && [ -z "$mode" ]; then
        # Only the digest: nothing about where alerts go changes.
        alerts_set_digest "$digest" "$digest_tz"
        config_apply DIGEST_AT
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

    [ -z "$digest" ] || alerts_set_digest "$digest" "$digest_tz"
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
