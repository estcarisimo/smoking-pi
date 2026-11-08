# shellcheck shell=bash
# smoking-pi openclaw: `openclaw`, the OpenClaw on this machine.
# Sourced by cli/smoking-pi, never run on its own.

# docs/openclaw-integration.md is six steps, each with a trap that makes a
# broken integration look like a working one. This runs the mechanical
# ones and ends on the only check that tells the two apart.

# OpenClaw belongs to a user, not to root: its config is ~/.openclaw, its
# gateway is usually a `systemctl --user` unit, and nvm installs the
# command under ~/.nvm, which sudo's secure_path drops. A packaged install
# needs sudo to read the env file, so under sudo every openclaw step runs
# as the user who ran sudo. Run as root, `openclaw mcp set` would have
# registered the server in /root/.openclaw, which no gateway reads.
openclaw_user() {
    [ "$(id -u)" = 0 ] && [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ] \
        && echo "$SUDO_USER" || true
}

# The openclaw command that user runs: PATH first, then nvm's default
# version, then any nvm version (newest first), then the usual per-user
# bin directories. Empty if there is none.
openclaw_bin() {
    local user home alias f
    command -v openclaw 2>/dev/null && return 0
    user="$(openclaw_user)"
    home="$HOME"
    [ -n "$user" ] && home="$(getent passwd "$user" 2>/dev/null | cut -d: -f6)"
    [ -n "$home" ] || return 0
    alias="$(cat "$home/.nvm/alias/default" 2>/dev/null || true)"
    # `24` means v24.*, never v240; `24.18.0` means exactly that one.
    case "$alias" in
        v[0-9]*|[0-9]*)
            f="$(ls -d "$home/.nvm/versions/node/v${alias#v}"{,.*}/bin/openclaw 2>/dev/null | sort -V | tail -1 || true)"
            [ -n "$f" ] && { echo "$f"; return 0; } ;;
    esac
    f="$(ls -d "$home"/.nvm/versions/node/*/bin/openclaw 2>/dev/null | sort -V | tail -1 || true)"
    [ -n "$f" ] && { echo "$f"; return 0; }
    for f in "$home/.local/bin/openclaw" "$home/.npm-global/bin/openclaw" \
             "$home/.volta/bin/openclaw" "$home/.bun/bin/openclaw"; do
        [ -x "$f" ] && { echo "$f"; return 0; }
    done
    return 0
}

# Run "$@" as the OpenClaw user, with the directory of $OPENCLAW_BIN first
# on PATH (nvm keeps `node` beside it) and that user's session bus, so
# `systemctl --user restart openclaw-gateway` reaches their gateway.
as_openclaw_user() {
    local user uid path="$PATH"
    user="$(openclaw_user)"
    [ -z "${OPENCLAW_BIN:-}" ] || path="$(dirname "$OPENCLAW_BIN"):$PATH"
    if [ -z "$user" ]; then
        PATH="$path" "$@"
        return
    fi
    uid="$(id -u "$user")"
    sudo -u "$user" -H env PATH="$path" XDG_RUNTIME_DIR="/run/user/$uid" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" "$@"
}

openclaw_evidence() {
    # docs/openclaw-integration.md, "Verify with evidence, not with the
    # answer". A well-primed agent answers fluently from its own shell
    # while the MCP server sits untouched, so the reply proves nothing;
    # the server's own tool= lines are the only thing that does.
    OPENCLAW_BIN="$(openclaw_bin)"
    if [ -z "$OPENCLAW_BIN" ]; then
        local who; who="$(openclaw_user)"
        echo "No openclaw command for ${who:-$(id -un)} here, so there is nothing to ask." >&2
        return 2
    fi
    echo "Asking the agent one question, then reading the MCP server's log."
    echo "(The answer is not the test -- the log is.)"
    # Its output is kept, not shown: the answer proves nothing (above), but
    # when the agent could not answer at all, what it said is the reason --
    # its last lines only, which is where an error is, not a whole reply.
    # Bounded: a gateway that accepts and never answers would otherwise
    # hold this check forever (no timeout(1) on macOS: unbounded there).
    local said rc=0 bound=()
    command -v timeout >/dev/null 2>&1 && bound=(timeout 180)
    said="$(as_openclaw_user ${bound[@]+"${bound[@]}"} "$OPENCLAW_BIN" agent --agent main --session-key smoking-pi-check \
        -m "how has my connection been in the last 6 hours?" 2>&1)" || rc=$?
    local seen
    seen="$(compose logs mcp-server --since 3m 2>/dev/null | grep 'tool=' || true)"
    if [ -n "$seen" ]; then
        echo
        echo "Connected. The agent called the server:"
        printf '%s\n' "$seen" | tail -6 | sed 's/^/  /'
        return 0
    fi
    echo >&2
    if [ "$rc" != 0 ]; then
        # Not the trap below: the agent never answered, so the tool set and
        # the skill were never in play. Blaming them sent people the wrong
        # way (a stopped gateway read as "reload the MCP tools").
        if [ "$rc" = 124 ] && [ ${#bound[@]} -gt 0 ]; then
            echo "NOT connected: the agent did not answer within 3 minutes. It said:" >&2
        else
            echo "NOT connected: the agent did not answer (openclaw exited $rc). It said:" >&2
        fi
        printf '%s\n' "${said:-(nothing)}" | tail -6 | sed 's/^/  | /' >&2
        echo "Is the gateway running and set up? 'openclaw gateway status' says;" >&2
        echo "then run this check again." >&2
        return 1
    fi
    echo "NOT connected: the agent answered without calling the MCP server." >&2
    echo "It was answering from its own shell, which sounds right and is not" >&2
    echo "your recorded history. The usual causes, in order:" >&2
    echo "  1. The gateway still has its cached tool set. 'openclaw mcp reload'," >&2
    echo "     restart the gateway, and start a NEW chat session." >&2
    echo "  2. The skill is missing or stale, so nothing redirects the agent:" >&2
    echo "     $(cli_name) openclaw   (installs the current one)" >&2
    echo "  3. The server is not registered: 'openclaw mcp probe smokeping'." >&2
    echo "$SMOKING_PI_HOME/docs/openclaw-integration.md, 'Verify with evidence', has the detail." >&2
    return 1
}

# The skill is the release's answering guide (shared/modules/common/guide.py),
# which changes in most releases, so an upgrade leaves the installed copy
# behind: the agent goes on answering in the old shape and nothing says so
# (v2.32.0, both Pis, until the installer was run by hand). `upgrade` calls
# this last. A skill that was never installed stays uninstalled; a stale
# one is refreshed and the gateway reloaded, as step 5 of `openclaw` does --
# the upgrade has just recreated the MCP server under that gateway anyway.
# A copy tuned in place differs too and is replaced the same way: the
# installer keeps it as SKILL.md.bak, and this says so, by path -- one
# .bak only, so the next upgrade's would overwrite it. Never fails the
# upgrade: what it cannot do, it names.
# How long the installer waited for a restarted gateway, from its output
# ("did not answer within 45 s"); "its wait" when the line is not there.
openclaw_said_wait() {
    local n; n="$(printf '%s\n' "$1" | sed -n 's/.*did not answer within \([0-9][0-9]*\) s.*/\1/p' | tail -1)"
    [ -n "$n" ] && echo "$n s" || echo "its wait"
}

openclaw_refresh_skill() {
    local script="$SMOKING_PI_HOME/shared/scripts/install-openclaw-skill.sh"
    local source="$SMOKING_PI_HOME/examples/openclaw/smokeping-monitoring/SKILL.md"
    local user home dest said rc=0
    [ -x "$script" ] && [ -f "$source" ] || return 0
    user="$(openclaw_user)"
    if [ -n "$user" ]; then
        # The installer runs as that user through sudo, which drops
        # OPENCLAW_SKILLS_DIR: compare the file it will write, or a stale
        # skill elsewhere would be "refreshed" on every upgrade.
        home="$(getent passwd "$user" 2>/dev/null | cut -d: -f6 || true)"
        [ -n "$home" ] || return 0
        dest="$home/.openclaw/skills/smokeping-monitoring/SKILL.md"
    else
        dest="${OPENCLAW_SKILLS_DIR:-$HOME/.openclaw/skills}/smokeping-monitoring/SKILL.md"
    fi
    [ -f "$dest" ] || return 0
    cmp -s "$source" "$dest" && return 0
    echo
    echo "The OpenClaw skill in $dest differs from this release's;"
    echo "refreshing it and reloading the gateway (a few minutes on a 4 GB Pi)..."
    OPENCLAW_BIN="$(openclaw_bin)"
    said="$(as_openclaw_user "$script" --reload 2>&1)" || rc=$?
    if [ "$rc" != 0 ] && [ "$rc" != 3 ] && [ "$rc" != 4 ]; then
        echo "WARNING: it could not be refreshed, so the assistant answers from the old" >&2
        echo "one. The installer said:" >&2
        printf '%s\n' "${said:-(nothing)}" | tail -3 | sed 's/^/  | /' >&2
        echo "Check that ${user:-$(id -un)} owns $(dirname "$dest"), then refresh it," >&2
        echo "and reload the gateway, with:" >&2
        echo "  $(cli_name) openclaw" >&2
        return 0
    fi
    if [ "$rc" = 3 ]; then
        echo "Refreshed it. No gateway answered, so nothing reloaded: a gateway reads"
        echo "the new skill when it starts ('openclaw gateway status' says why)."
    elif [ "$rc" = 4 ]; then
        # The installer's "restarted, but not answering yet" (#449): the
        # restart was issued and succeeded; a slow gateway is still starting.
        echo "Refreshed it and restarted the gateway; its status probe did not answer"
        echo "within $(openclaw_said_wait "$said"), so it is still starting (a 4 GB Pi takes a"
        echo "while). It reads the new skill as it starts: 'openclaw gateway status'."
    elif [ -z "$OPENCLAW_BIN" ]; then
        echo "Refreshed it. No openclaw command here to reload the gateway with: unless"
        echo "it runs as a systemd user unit (restarted), restart it however you run it."
    else
        echo "Refreshed it and reloaded the gateway."
    fi
    echo "The copy it replaced is $dest.bak: if you tuned the skill in place (its"
    echo "description:), apply that to the new one -- the next refresh replaces the .bak."
    echo "Start a NEW chat session: an open one keeps the skill it started with."
}

# What of hooks.* is in the user's openclaw.json, one field a line: the token
# (empty when there is none or it is a reference to a secret elsewhere);
# "current" when the gateway is already set up for session key $1, else
# "stale"; "ref" when the token is such a reference, else "plain"; the prefix
# allowlist with that key in it; the agent allowlist to write (empty: leave it
# as it is); session.dmScope. A person's own hooks keep working, so what they
# allowed stays allowed (config-hooks: an empty prefix list allows everything,
# and an empty agent list denies everything, on purpose).
openclaw_hooks_read() {
    local user home="$HOME" f
    user="$(openclaw_user)"
    [ -z "$user" ] || home="$(getent passwd "$user" 2>/dev/null | cut -d: -f6)"
    f="$home/.openclaw/openclaw.json"
    command -v python3 >/dev/null || return 1
    python3 - "$f" "$1" <<'PY' 2>/dev/null
import json, sys
try:
    config = json.load(open(sys.argv[1]))
except FileNotFoundError:
    config = {}
except (OSError, ValueError):
    # Not read is not "no hooks": taken for that, it would replace the token
    # and the lists of a file with comments in it (OpenClaw's is JSON5).
    sys.exit(1)
key, hooks = sys.argv[2], config.get("hooks") or {}
token = hooks.get("token")
plain = isinstance(token, str) and not token.startswith("${")
prefixes = [p for p in hooks.get("allowedSessionKeyPrefixes") or [] if p]
agents = hooks.get("allowedAgentIds")
def covers(k):
    return any(k.lower().startswith(p.lower()) for p in prefixes)


# The gateway refuses to START with a prefix list that does not admit the
# key it generates when none is given: `hook:`, or defaultSessionKey when
# set (2026.11.1; staging's gateway crash-looped on ["agent:main:main"]).
default = hooks.get("defaultSessionKey") or ""
own = default if default else "hook:"
fits = covers(key) and (covers(own) if default else "hook:" in [p.lower() for p in prefixes])
current = (hooks.get("enabled") is True and token
           and hooks.get("allowRequestSessionKey") is True and fits)
if not fits:
    for needed in (own, key):
        if needed == "hook:" and "hook:" not in [p.lower() for p in prefixes]:
            prefixes.append("hook:")
        elif needed != "hook:" and not covers(needed):
            prefixes.append(needed)
if agents is None:
    # Nothing allowed yet: only ours. Hooks already on with no list are the
    # person's, and narrowing them could break what they run.
    write_agents = None if hooks.get("enabled") else ["main"]
elif agents and "main" not in agents and "*" not in agents:
    write_agents = [*agents, "main"]
    current = False
else:
    write_agents = None
print(token if plain else "")
print("current" if current else "stale")
print("ref" if token and not plain else "plain")
print(json.dumps(prefixes))
print(json.dumps(write_agents) if write_agents else "")
print((config.get("session") or {}).get("dmScope") or "main")
PY
}

# Which way an alert goes now, in words. The probe runs no turn: the gateway
# checks the token first and the body after, so an accepted token gets 400.
openclaw_push_status() {
    local push token url code
    push="$(env_get OPENCLAW_PUSH)"
    if [ "$push" != agent ]; then
        echo "message relay (hooks: not asked for, OPENCLAW_PUSH=${push:-message})"
        return 0
    fi
    token="$(env_get OPENCLAW_HOOKS_TOKEN)"
    if [ -z "$token" ]; then
        echo "message relay (hooks: no OPENCLAW_HOOKS_TOKEN)"
        return 0
    fi
    url="$(env_get OPENCLAW_URL)"
    code="$(printf 'url = "%s"\nrequest = "POST"\nsilent\nmax-time = 10\noutput = "/dev/null"\nwrite-out = "%%{http_code}"\ndata = "{}"\nheader = "Content-Type: application/json"\nheader = "Authorization: Bearer %s"\n' \
        "${url:-http://127.0.0.1:18789}/hooks/agent" "$(curl_cfg_quote "$token")" \
        | curl -K - 2>/dev/null || echo 000)"
    case "$code" in
        400) echo "agent (hooks accepted)" ;;
        401) echo "message relay (hooks: the token was rejected, 401)" ;;
        404) echo "message relay (hooks: not enabled on the gateway, 404)" ;;
        000*) echo "message relay (hooks: the gateway did not answer)" ;;
        *) echo "message relay (hooks: unexpected HTTP $code)" ;;
    esac
}

# Set up the hooks block and point the alerter at it. The token goes through
# a 0600 file, as the MCP registration does: sudo logs the command it runs.
openclaw_enable_hooks() {
    local key out token state kind prefixes agents scope spec user failed=0
    key="$(env_get OPENCLAW_SESSION)"; key="${key:-agent:main:main}"
    if ! [[ "$key" =~ ^[A-Za-z0-9:._-]+$ ]]; then
        echo "OPENCLAW_SESSION has characters a session key cannot have." >&2
        return 1
    fi
    if ! out="$(openclaw_hooks_read "$key")" || [ -z "$out" ]; then
        echo "Could not read ~/.openclaw/openclaw.json as plain JSON (comments or trailing" >&2
        echo "commas?), or python3 is missing, so alerts stay on the message relay." >&2
        echo "Set up its hooks by hand: docs/openclaw-integration.md, 'Push through the agent'." >&2
        return 1
    fi
    { IFS= read -r token; IFS= read -r state; IFS= read -r kind
      IFS= read -r prefixes; IFS= read -r agents; IFS= read -r scope; } <<< "$out"
    if [ "$scope" != main ] && [ -z "$(env_get OPENCLAW_SESSION)" ]; then
        echo "WARNING: session.dmScope is $scope, so your chat is not agent:main:main and a" >&2
        echo "         follow-up will not see a push. Find your chat's session key with" >&2
        echo "         'openclaw sessions', then: $(cli_name) config set OPENCLAW_SESSION KEY" >&2
        echo "         and $(cli_name) openclaw --push agent" >&2
    fi
    [ -n "$token" ] || token="$(env_get OPENCLAW_HOOKS_TOKEN)"
    if [ "$kind" = ref ] && [ -z "$token" ]; then
        # Its value lives somewhere this command cannot read, and writing a
        # new one over the reference would break whoever else uses it.
        echo "hooks.token in your OpenClaw config is a reference to a secret, so its value" >&2
        echo "is not readable here; alerts stay on the message relay. Give it to the alerter:" >&2
        echo "  $(cli_name) config set OPENCLAW_HOOKS_TOKEN" >&2
        echo "then: $(cli_name) openclaw --push agent" >&2
        return 1
    fi
    [ -n "$token" ] || token="$(openssl rand -hex 32)"
    [ -n "$token" ] || { echo "Could not generate a hooks token (openssl)." >&2; return 1; }
    if [ "$state" != current ]; then
        # One `config patch`, not a `config set` per key: each write goes
        # through the running gateway, which may reload on it, and the next
        # one then finds it gone (2026.11.1 on staging). The token is in the
        # 0600 file, never in an argv: sudo logs the command, ps shows it.
        local said
        spec="$(mktemp)"
        printf '%s' "$token" | python3 -c '
import json, sys
token, kind, prefixes, agents = sys.stdin.read(), sys.argv[1], sys.argv[2], sys.argv[3]
hooks = {"enabled": True, "allowRequestSessionKey": True,
         "allowedSessionKeyPrefixes": json.loads(prefixes)}
if kind != "ref":
    hooks["token"] = token
if agents:
    hooks["allowedAgentIds"] = json.loads(agents)
print(json.dumps({"hooks": hooks}))' "$kind" "$prefixes" "$agents" > "$spec" || failed=1
        user="$(openclaw_user)"
        [ -z "$user" ] || chown "$user" "$spec" 2>/dev/null || true
        if [ "$failed" = 0 ]; then
            said="$(as_openclaw_user "$OPENCLAW_BIN" config patch --file "$spec" 2>&1)" || failed=1
        fi
        rm -f "$spec"
        # Saved, but a busy gateway did not take it (2026.11.1 under load on
        # staging): the file has the hooks, the gateway needs a restart to
        # read them, and the alerter needs the token that is now in the file.
        case "$said" in
            *"persisted but was not applied"*)
                failed=0
                said="Restart the gateway to apply. ($said)" ;;
        esac
        if [ "$failed" = 1 ]; then
            echo "'openclaw config patch' of hooks failed; alerts stay on the message relay." >&2
            echo "OpenClaw 2026.10 or later has these hooks: 'openclaw --version' says which is here." >&2
            said="${said//"$token"/<hooks token>}"
            printf '%s\n' "${said:-}" | tail -3 | sed 's/^/  | /' >&2
            return 1
        fi
        # `hooks` hot-reloads: restart only when OpenClaw says this one did
        # not. On a 4 GB Pi a 2026.11 gateway restart is the heavy step: two
        # close together stalled staging past its hardware watchdog (11-07).
        case "$said" in
            *"Restart the gateway to apply"*)
                as_openclaw_user "$OPENCLAW_BIN" gateway restart >/dev/null 2>&1 \
                    || echo "WARNING: 'openclaw gateway restart' failed; restart the gateway however you run it." >&2 ;;
        esac
    fi
    env_set OPENCLAW_HOOKS_TOKEN "$token"
    env_set OPENCLAW_PUSH agent
}

# The alerter reads these at start: recreate it if it runs here at all.
openclaw_recreate_alerter() {
    case ",$(profiles)," in
        *,alerts,*) ;;
        *) echo "The alerter is not enabled; '$(cli_name) alerts --openclaw' turns it on with this path."
           return 0 ;;
    esac
    compose up -d --no-deps alerter >/dev/null 2>&1 \
        || echo "WARNING: the alerter was not recreated: $(cli_name) logs alerter" >&2
    [ "$(env_get NOTIFY_MODE)" = openclaw ] \
        || echo "NOTIFY_MODE is not openclaw, so alerts do not use this yet: $(cli_name) alerts --openclaw"
}

# A gateway just restarted takes a moment: ask a few times before saying the
# hooks do not answer.
openclaw_report_push() {
    local status i
    for i in 1 2 3 4 5 6 7 8; do
        status="$(openclaw_push_status)"
        case "$status" in *"did not answer"*) sleep "${OPENCLAW_PROBE_SLEEP:-2}" ;; *) break ;; esac
    done
    echo "Alert push: $status"
}

# `--push agent|message`: which way alerts go, without pairing again.
openclaw_switch_push() {
    case "$1" in
        agent)
            OPENCLAW_BIN="$(openclaw_bin)"
            if [ -z "$OPENCLAW_BIN" ]; then
                echo "No 'openclaw' command on this machine, so the hooks cannot be set up here." >&2
                return 1
            fi
            openclaw_enable_hooks || return 1 ;;
        message) env_set OPENCLAW_PUSH message ;;
        *) echo "--push takes agent or message" >&2; return 2 ;;
    esac
    openclaw_recreate_alerter
    openclaw_report_push
    openclaw_automations_follow
}

# After --push: the weekly is the agent's with agent, the alerter's with
# message, so automations already set up follow. None set up: none created.
openclaw_automations_follow() {
    OPENCLAW_BIN="${OPENCLAW_BIN:-$(openclaw_bin)}"
    [ -n "$OPENCLAW_BIN" ] && command -v python3 >/dev/null || return 0
    OC_JOBS="$(openclaw_jobs)" || return 0
    [ -n "$(openclaw_job_field heartbeat 2)$(openclaw_job_field weekly 2)" ] || return 0
    echo
    openclaw_automations_sync "" || true
}

# --- Automations: the heartbeat and the weekly, through the agent ------------
# docs/openclaw-integration.md, "Heartbeat and weekly through the agent".
# Two OpenClaw automations, each behind a condition script the gateway runs
# before any model turn. The script asks the MCP server (get_updates_since,
# get_weekly_summary) and wakes the agent only when the tool says there is
# something to say, so a quiet hour is one MCP call and no model quota. The
# script's saved state (OpenClaw's trigger.state) is the memory between runs:
# where the last look ended, and the day the heartbeat last spoke. A note in a
# prompt is not state, and an isolated turn remembers nothing.

OPENCLAW_HEARTBEAT_NAME="Smoking Pi heartbeat"
OPENCLAW_WEEKLY_NAME="Smoking Pi weekly"
OPENCLAW_MANAGED="Managed by smoking-pi openclaw automations"
# What each turn may call: only what it reads. The gate scripts reach the MCP
# server only through tools named here (server__tool).
OPENCLAW_HEARTBEAT_TOOLS="smokeping__get_updates_since,smokeping__get_notification_history"
OPENCLAW_WEEKLY_TOOLS="smokeping__get_weekly_summary"

# Minutes since midnight of an HH:MM; empty when it is not one.
hhmm_minutes() {
    [[ "${1:-}" =~ ^([01]?[0-9]|2[0-3]):([0-5][0-9])$ ]] || return 0
    echo $(( 10#${BASH_REMATCH[1]} * 60 + 10#${BASH_REMATCH[2]} ))
}

minutes_hhmm() { printf '%02d:%02d' $(( $1 / 60 )) $(( $1 % 60 )); }

# The night, as "FROM UNTIL" in minutes: the alerter's quiet hours when both
# are set, else 23:00-07:00 (the notification policy's night).
openclaw_night() {
    local from until
    from="$(hhmm_minutes "$(env_get QUIET_FROM)")"
    until="$(hhmm_minutes "$(env_get QUIET_UNTIL)")"
    if [ -z "$from" ] || [ -z "$until" ] || [ "$from" = "$until" ]; then
        from=1380 until=420
    fi
    echo "$from $until"
}

# The zone the automations' crons run in: the alerter's (DIGEST_TZ, then TZ,
# then UTC), never the gateway host's, so the night and the week agree.
openclaw_zone() {
    local tz; tz="$(env_get DIGEST_TZ)"
    [ -n "$tz" ] || tz="$(env_get TZ)"
    [[ "$tz" =~ ^[A-Za-z0-9_+/-]+$ ]] && echo "$tz" || echo UTC
}

# The heartbeat's hours, comma-separated in numeric order: every $1th whole
# hour of the day, counted from the first one after the night ($2 $3).
openclaw_day_hours() {
    local step=$1 from=$2 until=$3 start k h m i=0 out=()
    start=$(( (until + 59) / 60 ))
    for ((k = 0; k < 24; k++)); do
        h=$(( (start + k) % 24 )); m=$(( h * 60 ))
        if (( from > until )); then
            (( m >= from || m < until )) && continue
        else
            (( m >= from && m < until )) && continue
        fi
        (( i % step == 0 )) && out+=("$h")
        i=$(( i + 1 ))
    done
    # Runs of three or more as cron ranges: 7-22, not 7,8,...,22.
    printf '%s\n' "${out[@]}" | sort -n | awk '
        function flush() { if (n == "") return
            s = (n == p) ? n : (p == n + 1) ? n "," p : n "-" p
            o = o (o == "" ? "" : ",") s }
        { if (n != "" && $1 == p + 1) { p = $1; next } flush(); n = p = $1 }
        END { flush(); print o }'
}

openclaw_heartbeat_gate() {
    # $1 zone, $2 night from, $3 night until (minutes), $4 first window.
    cat <<JS
// The Smoking Pi heartbeat's gate, written by \`smoking-pi openclaw automations\`.
// It wakes the agent only when get_updates_since says worth_mentioning, at
// most once a day, never at night. trigger.state keeps the cursor (where the
// last look ended) and the day it last spoke.
const TZ = "$1";
const NIGHT = [$2, $3];
const FIRST = "$4";
const s = trigger.state || {};
const now = Date.now();
const p = Object.fromEntries(new Intl.DateTimeFormat("en-CA", {
  timeZone: TZ || undefined, year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hourCycle: "h23",
}).formatToParts(new Date(now)).map((x) => [x.type, x.value]));
const day = p.year + "-" + p.month + "-" + p.day;
const minute = Number(p.hour) * 60 + Number(p.minute);
const night = NIGHT[0] > NIGHT[1]
  ? minute >= NIGHT[0] || minute < NIGHT[1]
  : minute >= NIGHT[0] && minute < NIGHT[1];
// At night nothing is read, so the morning's first look covers the night
// (at most two days back).
if (night) return { fire: false, state: s };
const since = s.cursor && now - Date.parse(s.cursor) < 172800000 ? s.cursor : FIRST;
const res = await MCP.smokeping.getUpdatesSince({ since, include_non_material: false });
const text = res && res.content && res.content[0] && res.content[0].text;
if (!text || res.isError) throw new Error("get_updates_since failed: " + String(text).slice(0, 200));
const u = JSON.parse(text);
if (u.error) throw new Error("get_updates_since: " + u.error);
const last = { at: u.as_of, verdict: u.verdict, reasons: u.reasons || [] };
// The tool's own as_of, to the second, is the next since: windows meet
// without overlapping (common/updates.py).
const next = { cursor: u.as_of, spoke_on: s.spoke_on, last };
if (u.verdict !== "worth_mentioning") return { fire: false, state: next };
// One unsolicited message a day. What is held keeps the cursor where it was,
// so tomorrow's first look still covers it.
if (s.spoke_on === day) return { fire: false, state: { cursor: s.cursor, spoke_on: s.spoke_on, last: { ...last, held: "spoke today" } } };
return {
  fire: true,
  state: { ...next, spoke_on: day },
  message: "\n\nWindow: since=\"" + since + "\". The check saw verdict worth_mentioning ("
    + last.reasons.join(", ") + ").",
};
JS
}

openclaw_weekly_gate() {
    cat <<'JS'
// The Smoking Pi weekly's gate, written by `smoking-pi openclaw automations`.
// It wakes the agent only when the last week had something material, once
// per week; trigger.state keeps the week it last narrated. It runs three
// hours in a row, so a failed look or turn is tried again the same morning.
const s = trigger.state || {};
const res = await MCP.smokeping.getWeeklySummary({ week: "last" });
const text = res && res.content && res.content[0] && res.content[0].text;
if (!text || res.isError) throw new Error("get_weekly_summary failed: " + String(text).slice(0, 200));
const w = JSON.parse(text);
if (w.error) throw new Error("get_weekly_summary: " + w.error);
const week = (w.week && w.week.start) || "";
const last = { week, has_material: !!w.has_material, verdict: w.verdict };
if (!w.has_material || s.narrated === week) return { fire: false, state: { narrated: s.narrated, last } };
return {
  fire: true,
  state: { narrated: week, last },
  message: "\n\nThe week: " + ((w.week && w.week.label) || week) + ".",
};
JS
}

# The prompts. docs/openclaw-integration.md quotes both verbatim; a test keeps
# them in step.
openclaw_heartbeat_prompt() {
    # $1 first window, $2 night from, $3 night until (HH:MM), $4 zone words.
    cat <<EOF
You are the Smoking Pi heartbeat: an unattended check of this home's internet monitor. Your final reply is posted to the owner's chat exactly as you write it.
1. Call smokeping__get_updates_since with since set to the window given at the end of this message (if none is given, since="$1") and include_non_material=true.
2. If the call fails, or its verdict is nothing_new or material_pushed_already, reply exactly NO_REPLY.
3. If the local time in its as_of is between $2 and $3 ($4), reply exactly NO_REPLY: nothing unsolicited at night.
4. Otherwise the verdict is worth_mentioning: write 1 to 3 short sentences in plain English about what its reasons point to (a recurring loss window that appeared or went away, a cluster of short losses and where they were, the monitor blind with nothing said, or an episode nobody was told about). Say where in the tool's words: your Wi-Fi, your line, your provider, or the destinations it names. Give times as the tool prints them, and end with the episode's link when it has one.
Never say that all is well, never repeat anything listed in already_pushed, never give a cause the tool did not give. No greeting, no headings, no lists.
EOF
}

openclaw_weekly_prompt() {
    cat <<'EOF'
You are the Smoking Pi weekly: an unattended look back at this home's internet over the last week. Your final reply is posted to the owner's chat exactly as you write it.
1. Call smokeping__get_weekly_summary with week="last".
2. If the call fails or has_material is false, reply exactly NO_REPLY.
3. Otherwise narrate the week in 6 to 10 sentences of plain English prose, without bullet points, headings or jargon. Say whether it was a good, fair or bad week (verdict good, mixed or bad) against the week before, by cause in the words of each by_cause cause_text (your Wi-Fi, your line, your provider, specific destinations), with minutes this week and the week before. Name the recurring patterns that are still active, with their hours and days. Say how much of the week the monitor measured (monitor.measured_pct), why it was blind when it was, and what the watchdog did. Name at most 3 destinations with the most loss of their own. End with links.grafana_week when there is one.
Give times as the tool prints them. Never invent a number the tool did not give.
EOF
}

# The jobs this command manages, and OpenClaw's own heartbeat, one per line:
# role, id, enabled, schedule, zone, delivery (ours|other|none), the run
# history's last (time, status, delivery), the gate's last look, and the
# description. Fails when the list cannot be read.
openclaw_jobs() {
    local json
    local bound=()
    # Bounded: a gateway that is down must not hold `alerts` or `disable`.
    command -v timeout >/dev/null 2>&1 && bound=(timeout 60)
    json="$(as_openclaw_user ${bound[@]+"${bound[@]}"} "$OPENCLAW_BIN" automations list --all --json 2>/dev/null)" || return 1
    [ -n "$json" ] || return 1
    OC_TO="$(env_get OPENCLAW_TO)" OC_HB="$OPENCLAW_HEARTBEAT_NAME" OC_WK="$OPENCLAW_WEEKLY_NAME" \
    python3 -c '
import json, os, sys
from datetime import datetime
try:
    jobs = json.loads(sys.stdin.read()).get("jobs") or []
except ValueError:
    sys.exit(1)
roles = {os.environ["OC_HB"]: "heartbeat", os.environ["OC_WK"]: "weekly"}
def when(ms):
    return datetime.fromtimestamp(ms / 1000).astimezone().strftime("%Y-%m-%d %H:%M %Z") if ms else "-"
def clean(v):
    return str(v if v is not None else "-").replace("\t", " ").replace("\n", " ") or "-"
for j in jobs:
    role = roles.get(j.get("name"))
    if role is None and str(j.get("declarationKey") or "").startswith("heartbeat:") \
            and (j.get("payload") or {}).get("kind") == "heartbeat":
        role = "builtin"
    if role is None:
        continue
    sched = j.get("schedule") or {}
    expr = sched.get("expr") or ("every %dm" % (sched.get("everyMs", 0) // 60000))
    to = (j.get("delivery") or {}).get("to")
    dest = "none" if not to else ("ours" if to == os.environ.get("OC_TO") else "other")
    st = j.get("state") or {}
    run = st.get("lastRunStatus") or "-"
    deliv = st.get("lastDeliveryStatus") or "-"
    if st.get("lastDeliverySuppressionReason") or st.get("deliverySuppressionReason"):
        deliv = "suppressed"
    gate = st.get("triggerState") or {}
    look = gate.get("last") or {}
    seen = "-"
    if look:
        seen = "%s at %s" % (look.get("verdict") or ("material" if look.get("has_material") else "nothing material"),
                             look.get("at") or look.get("week") or "-")
        if look.get("held"):
            seen += " (held: " + look["held"] + ")"
    print("\t".join(clean(v) for v in (role, j.get("id"), "on" if j.get("enabled") else "off",
          expr, sched.get("tz"), dest, when(st.get("lastRunAtMs")), run, deliv,
          when(st.get("lastTriggerEvalAtMs")), seen, j.get("description"))))
' <<< "$json"
}

# Field $2 (1-based) of role $1's line in $OC_JOBS.
openclaw_job_field() {
    printf '%s\n' "$OC_JOBS" | awk -F'\t' -v r="$1" -v f="$2" '$1 == r { print $f; exit }'
}

# Create or update one job: $1 role, $2 name, $3 cron, $4 zone, $5 prompt,
# $6 gate script, $7 tools, $8 what the description says about it. A job that
# already matches is left alone: an edit of the gate resets its saved state
# (2026.11.1), which would forget the cursor and the day it last spoke.
openclaw_put_job() {
    local role=$1 name=$2 cron=$3 tz=$4 prompt=$5 gate=$6 tools=$7 what=$8
    local to channel id desc sum gatefile user said rc=0 args=()
    to="$(env_get OPENCLAW_TO)"
    channel="$(env_get OPENCLAW_CHANNEL)"; channel="${channel:-telegram}"
    sum="$(printf '%s\n' "$cron" "$tz" "$prompt" "$gate" "$tools" "$to" "$channel" \
        | sha256sum | cut -c1-12)"
    desc="$OPENCLAW_MANAGED ($what; spec $sum)"
    id="$(openclaw_job_field "$role" 2)"
    if [ -n "$id" ] && [ "$(openclaw_job_field "$role" 12)" = "$desc" ] \
        && [ "$(openclaw_job_field "$role" 3)" = on ]; then
        echo "  $name: up to date ($what)."
        return 0
    fi
    gatefile="$(mktemp)"
    printf '%s\n' "$gate" > "$gatefile"
    user="$(openclaw_user)"
    [ -z "$user" ] || chown "$user" "$gatefile" 2>/dev/null || true
    chmod 644 "$gatefile"
    args=(--name "$name" --description "$desc" --cron "$cron" --session isolated
          --agent main --message "$prompt" --trigger-script "$gatefile" --tools "$tools"
          --light-context --announce --channel "$channel" --to "$to")
    [ -z "$tz" ] || args+=(--tz "$tz")
    if [ -n "$id" ]; then
        said="$(as_openclaw_user "$OPENCLAW_BIN" automations edit "$id" "${args[@]}" --enable 2>&1)" || rc=$?
    else
        said="$(as_openclaw_user "$OPENCLAW_BIN" automations add "${args[@]}" --json 2>&1)" || rc=$?
    fi
    rm -f "$gatefile"
    if [ "$rc" != 0 ]; then
        echo "  WARNING: '$name' could not be $([ -n "$id" ] && echo updated || echo created). OpenClaw said:" >&2
        said="${said//"$to"/<chat>}"
        printf '%s\n' "${said:-(nothing)}" | tail -3 | sed 's/^/    | /' >&2
        return 1
    fi
    echo "  $name: $([ -n "$id" ] && echo updated || echo created) ($what)."
}

openclaw_rm_job() {
    # $1 role, $2 name, $3 why (words).
    local id
    id="$(openclaw_job_field "$1" 2)"
    [ -n "$id" ] || return 0
    if as_openclaw_user "$OPENCLAW_BIN" automations rm "$id" >/dev/null 2>&1; then
        echo "  $2: removed ($3)."
    else
        echo "  WARNING: '$2' could not be removed: openclaw automations list, then rm it." >&2
        return 1
    fi
}

# Create, update or remove the two automations to match the env file.
# $1: the heartbeat's interval in hours (1, 3, 6), or empty for the one it has.
openclaw_automations_sync() {
    local hours=${1:-} tz night from until day_hours zone_words weekly_on schedule failed=0
    local wday wat wmin cron
    if [ -z "$(env_get OPENCLAW_TO)" ]; then
        echo "Automations: none, because OPENCLAW_TO (the chat they post to) is not set:"
        echo "  $(cli_name) alerts --openclaw --to RECIPIENT, then $(cli_name) openclaw automations"
        return 1
    fi
    if ! OC_JOBS="$(openclaw_jobs)"; then
        echo "WARNING: 'openclaw automations list' did not answer, so the heartbeat and the" >&2
        echo "weekly were not set up. OpenClaw 2026.11 or later has them; is the gateway running?" >&2
        return 1
    fi
    if [ -z "$hours" ]; then
        hours="$(openclaw_job_field heartbeat 12 | sed -n 's/.*every \([0-9]*\)h.*/\1/p')"
        hours="${hours:-1}"
    fi
    tz="$(openclaw_zone)"
    zone_words="${tz:-the gateway host time}"
    night="$(openclaw_night)"; from="${night% *}"; until="${night#* }"
    day_hours="$(openclaw_day_hours "$hours" "$from" "$until")"
    if [ -z "$day_hours" ]; then
        echo "The quiet hours leave no whole hour of the day for the heartbeat: $(cli_name) alerts --quiet 23:00-07:00" >&2
        return 1
    fi
    echo "Automations (in $zone_words; night $(minutes_hhmm "$from")-$(minutes_hhmm "$until")):"
    openclaw_put_job heartbeat "$OPENCLAW_HEARTBEAT_NAME" "0 $day_hours * * *" "$tz" \
        "$(openclaw_heartbeat_prompt "${hours}h" "$(minutes_hhmm "$from")" "$(minutes_hhmm "$until")" \
            "$zone_words")" \
        "$(openclaw_heartbeat_gate "$tz" "$from" "$until" "${hours}h")" \
        "$OPENCLAW_HEARTBEAT_TOOLS" "every ${hours}h, at $day_hours" || failed=1

    # The weekly is the agent's only with the agent push: otherwise the alerter
    # sends its own (alerter/weekly.py), and both would say the week. Whether
    # it is on, and when, is the alerter's reading (alerts_weekly_schedule:
    # WEEKLY_*, or the retired daily digest's settings), not WEEKLY_ENABLED
    # alone: on an install upgraded with DIGEST_ENABLED=true this said the
    # weekly was off while the alerter sent it (#450).
    weekly_on=0; schedule=""
    if [ "$(env_get NOTIFY_MODE)" = openclaw ] && [ "$(env_get OPENCLAW_PUSH)" = agent ] \
        && schedule="$(alerts_weekly_schedule)"; then
        weekly_on=1
    fi
    if [ "$weekly_on" = 1 ]; then
        wday="${schedule%% *}"; wat="${schedule#* }"; wat="${wat%% *}"
        case "$wday" in sun) wday=0 ;; mon) wday=1 ;; tue) wday=2 ;; wed) wday=3 ;;
                        thu) wday=4 ;; fri) wday=5 ;; sat) wday=6 ;; *) wday=1 ;; esac
        wmin="$(hhmm_minutes "$wat")"; wmin="${wmin:-480}"
        # The slot and the two hours after it, before midnight: the gate
        # narrates a week once, so the later two only retry a failed one.
        local h=$((wmin / 60)) hs
        hs="$h"; (( h + 1 < 24 )) && hs="$hs,$((h + 1))"; (( h + 2 < 24 )) && hs="$hs,$((h + 2))"
        cron="$((wmin % 60)) $hs * * $wday"
        if openclaw_put_job weekly "$OPENCLAW_WEEKLY_NAME" "$cron" "$tz" "$(openclaw_weekly_prompt)" \
            "$(openclaw_weekly_gate)" "$OPENCLAW_WEEKLY_TOOLS" \
            "${schedule%% *} at $wat"; then
            openclaw_weekly_owner agent
        else
            failed=1
            openclaw_weekly_owner alerter
        fi
    elif [ -n "$(openclaw_job_field weekly 2)" ]; then
        openclaw_rm_job weekly "$OPENCLAW_WEEKLY_NAME" \
            "the alerter sends the weekly without OPENCLAW_PUSH=agent, or it is off" || failed=1
        openclaw_weekly_owner alerter
    else
        openclaw_weekly_owner alerter
        if ! alerts_weekly_schedule >/dev/null; then
            echo "  $OPENCLAW_WEEKLY_NAME: not set up, the weekly is off ($(cli_name) alerts --weekly mon 08:00)."
        else
            echo "  $OPENCLAW_WEEKLY_NAME: not set up; the alerter sends the weekly (OPENCLAW_PUSH is not agent)."
        fi
    fi
    if [ "$(openclaw_job_field builtin 3)" = on ]; then
        echo "  OpenClaw's own 'Heartbeat (main)' still runs a model turn $(openclaw_job_field builtin 4) without"
        echo "  looking at this Pi. To save quota: $(cli_name) openclaw automations --disable-builtin-heartbeat"
    fi
    return "$failed"
}

openclaw_automations_remove() {
    local failed=0
    if ! OC_JOBS="$(openclaw_jobs)"; then
        echo "WARNING: 'openclaw automations list' did not answer; nothing was removed." >&2
        return 1
    fi
    if [ -z "$(openclaw_job_field heartbeat 2)$(openclaw_job_field weekly 2)" ]; then
        echo "No Smoking Pi automations in OpenClaw."
        return 0
    fi
    openclaw_rm_job heartbeat "$OPENCLAW_HEARTBEAT_NAME" "asked to" || failed=1
    if openclaw_rm_job weekly "$OPENCLAW_WEEKLY_NAME" "asked to"; then
        openclaw_weekly_owner alerter
    else
        failed=1
    fi
    return "$failed"
}

# For `disable mcp` and `purge`: the gates call the MCP server, so without it
# every run would fail. Quiet when there is no openclaw or nothing to remove.
openclaw_automations_cleanup() {
    OPENCLAW_BIN="$(openclaw_bin)"
    [ -n "$OPENCLAW_BIN" ] || return 0
    OC_JOBS="$(openclaw_jobs)" || return 0
    [ -n "$(openclaw_job_field heartbeat 2)$(openclaw_job_field weekly 2)" ] || return 0
    echo "Removing the Smoking Pi automations from OpenClaw (they read the MCP server):"
    openclaw_rm_job heartbeat "$OPENCLAW_HEARTBEAT_NAME" "no MCP server" || true
    if openclaw_rm_job weekly "$OPENCLAW_WEEKLY_NAME" "no MCP server" && [ "${1:-}" != purge ]; then
        openclaw_weekly_owner alerter
    fi
}

# OPENCLAW_WEEKLY: who sends the weekly with the agent push. The alerter
# cannot see OpenClaw, so it skips its own weekly only on this, written here
# when the automation exists: a weekly is never lost to an automation that
# was never made (an old gateway, an install that did not run this).
openclaw_weekly_owner() {
    local now; now="$(env_get OPENCLAW_WEEKLY)"
    [ "${now:-alerter}" = "$1" ] && return 0
    env_set OPENCLAW_WEEKLY "$1"
    case ",$(profiles)," in
        *,alerts,*) compose up -d --no-deps alerter >/dev/null 2>&1 \
            || echo "WARNING: the alerter was not recreated: $(cli_name) logs alerter" >&2 ;;
    esac
    if [ "$1" = agent ]; then
        echo "  The weekly is the automation's now; the alerter no longer sends it."
    else
        echo "  The alerter sends the weekly again."
    fi
}

# OpenClaw's own heartbeat is a model turn every 30 min or hour that reads
# HEARTBEAT.md, not this Pi. Its job is the gateway's (`automations rm` would
# be recreated); 0m is OpenClaw's documented off switch, kept across restarts.
openclaw_disable_builtin_heartbeat() {
    local said
    if said="$(as_openclaw_user "$OPENCLAW_BIN" config set agents.defaults.heartbeat.every 0m 2>&1)"; then
        echo "OpenClaw's own heartbeat is off (agents.defaults.heartbeat.every = 0m)."
        echo "'openclaw config unset agents.defaults.heartbeat.every' turns it back on."
    else
        echo "WARNING: 'openclaw config set agents.defaults.heartbeat.every 0m' failed:" >&2
        printf '%s\n' "${said:-(nothing)}" | tail -3 | sed 's/^/  | /' >&2
        return 1
    fi
}

# What --check says about them.
openclaw_automations_report() {
    local role name line
    OPENCLAW_BIN="${OPENCLAW_BIN:-$(openclaw_bin)}"
    if [ -z "$OPENCLAW_BIN" ]; then
        echo "Automations: no openclaw command here to ask."
        return 0
    fi
    if ! OC_JOBS="$(openclaw_jobs)"; then
        echo "Automations: 'openclaw automations list' did not answer."
        return 0
    fi
    echo "Automations:"
    for role in heartbeat weekly builtin; do
        case "$role" in
            heartbeat) name="$OPENCLAW_HEARTBEAT_NAME" ;;
            weekly) name="$OPENCLAW_WEEKLY_NAME" ;;
            builtin) name="OpenClaw's own Heartbeat (main)" ;;
        esac
        line="$(printf '%s\n' "$OC_JOBS" | awk -F'\t' -v r="$role" '$1 == r { print; exit }')"
        if [ -z "$line" ]; then
            case "$role" in
                builtin) ;;
                weekly)
                    # Expected only with the agent push and the weekly on,
                    # as the alerter reads it (DIGEST_ENABLED=true counts).
                    if [ "$(env_get OPENCLAW_PUSH)" != agent ]; then
                        echo "  $name: none; the alerter sends the weekly (OPENCLAW_PUSH is not agent)"
                    elif ! alerts_weekly_schedule >/dev/null; then
                        echo "  $name: none, the weekly is off ($(cli_name) alerts --weekly mon 08:00)"
                    else
                        echo "  $name: missing ($(cli_name) openclaw automations)"
                    fi ;;
                *) echo "  $name: missing ($(cli_name) openclaw automations)" ;;
            esac
            continue
        fi
        IFS=$'\t' read -r _ _ on sched zone dest ran status deliv looked seen _ <<< "$line"
        if [ "$role" = builtin ]; then
            echo "  $name: $on, $sched, a model turn each time that does not look at this Pi"
            continue
        fi
        case "$dest" in
            ours) dest="to the alerts' chat (OPENCLAW_TO)" ;;
            other) dest="to ANOTHER chat than OPENCLAW_TO: $(cli_name) openclaw automations" ;;
            *) dest="delivering nowhere: $(cli_name) openclaw automations" ;;
        esac
        [ "$zone" = - ] && zone="gateway time"
        echo "  $name: $on, '$sched' ($zone), $dest"
        if [ "$ran" = - ]; then
            echo "    last run: none yet (the agent runs only when the check finds something)"
        else
            echo "    last run: $ran, $status, delivery $deliv"
        fi
        [ "$looked" = - ] || echo "    last check: $looked, $seen"
    done
}

cmd_openclaw_automations() {
    local hours="" disable=0 builtin=0
    while [ $# -gt 0 ]; do
        case "$1" in
            --interval)
                [ $# -ge 2 ] || { echo "--interval takes 1h, 3h or 6h" >&2; return 2; }
                case "$2" in 1h|3h|6h) hours="${2%h}" ;;
                    *) echo "--interval takes 1h, 3h or 6h" >&2; return 2 ;; esac
                shift 2 ;;
            --disable) disable=1; shift ;;
            --disable-builtin-heartbeat) builtin=1; shift ;;
            -h|--help)
                echo "smoking-pi openclaw automations [--interval 1h|3h|6h] [--disable] [--disable-builtin-heartbeat]"
                echo "  The heartbeat (anything new? at most one message a day, never at night)"
                echo "  and, with --push agent, the weekly, as OpenClaw automations posting to"
                echo "  OPENCLAW_TO. --interval: how often the heartbeat looks (default 1h, or"
                echo "  the one it has); a look that finds nothing costs no model turn."
                echo "  --disable removes both. --disable-builtin-heartbeat turns off OpenClaw's"
                echo "  own heartbeat, a model turn every 30-60 min that never looks at this Pi."
                return 0 ;;
            *) echo "unknown option $1" >&2; return 2 ;;
        esac
    done
    if [ "$disable" = 1 ] && [ -n "$hours" ]; then
        echo "--disable and --interval do not go together" >&2; return 2
    fi
    OPENCLAW_BIN="$(openclaw_bin)"
    if [ -z "$OPENCLAW_BIN" ]; then
        echo "No 'openclaw' command on this machine, so there are no automations to set up." >&2
        return 1
    fi
    command -v python3 >/dev/null || { echo "python3 is needed to read OpenClaw's automations." >&2; return 1; }
    local rc=0
    if [ "$disable" = 1 ]; then
        openclaw_automations_remove || rc=1
    else
        openclaw_automations_sync "$hours" || rc=1
    fi
    if [ "$builtin" = 1 ]; then
        openclaw_disable_builtin_heartbeat || rc=1
    fi
    return "$rc"
}

cmd_openclaw() {
    local check=0 push=""
    case "${1:-}" in
        automations) shift; need_edition
            if [ "$SMOKING_PI_EDITION" != pro ]; then
                echo "The MCP server is a Pro service; the $SMOKING_PI_EDITION edition has no automations." >&2
                return 1
            fi
            cmd_openclaw_automations "$@"; return $? ;;
    esac
    while [ $# -gt 0 ]; do
        case "$1" in
            --check) check=1; shift ;;
            --push)
                [ $# -ge 2 ] || { echo "--push takes agent or message" >&2; return 2; }
                push="$2"; shift 2 ;;
            -h|--help)
                echo "smoking-pi openclaw [--check] [--push agent|message]"
                echo "smoking-pi openclaw automations [--interval 1h|3h|6h] [--disable] [--disable-builtin-heartbeat]"
                echo "  Connect this stack to an OpenClaw gateway on this machine, or say"
                echo "  exactly why it is not connected. --check only runs the verification,"
                echo "  and says which way alerts go. --push switches that without pairing"
                echo "  again. agent: the agent writes each alert in your chat, so you can"
                echo "  reply to it; message: the alerter's fixed text, relayed."
                echo "  It also sets up the heartbeat and the weekly as OpenClaw automations:"
                echo "  smoking-pi openclaw automations --help."
                return 0 ;;
            *) echo "unknown option $1" >&2; return 2 ;;
        esac
    done
    need_edition
    # Every failure below returns; none exits. cmd_install calls this as a
    # subroutine, and a bash `exit` from here would take the whole install
    # down with it -- including the password summary that comes after --
    # which is the opposite of "every branch ends with a working install".
    if [ "$SMOKING_PI_EDITION" != pro ]; then
        echo "The MCP server is a Pro service; the $SMOKING_PI_EDITION edition does not ship it." >&2
        echo "Smoking Pi measures and alerts without OpenClaw -- nothing here is missing." >&2
        return 1
    fi
    [ -f "$ENV_FILE" ] || { echo "no env file at $ENV_FILE: run 'smoking-pi install' first" >&2; return 1; }

    if [ "$check" = 1 ]; then
        local check_rc=0
        openclaw_evidence || check_rc=$?
        echo
        openclaw_report_push
        echo
        openclaw_automations_report
        return "$check_rc"
    fi
    [ -z "$push" ] || { openclaw_switch_push "$push"; return $?; }

    OPENCLAW_BIN="$(openclaw_bin)"
    if [ -z "$OPENCLAW_BIN" ]; then
        # Not an error, and not a reason to stop: the stack is already
        # measuring. Two different situations, and the remote one has its
        # own guide -- saying "install OpenClaw" to someone whose gateway
        # is on their laptop sends them the wrong way.
        echo "No 'openclaw' command on this machine."
        echo
        echo "If OpenClaw runs on ANOTHER machine, that is the supported case and it"
        echo "needs a tunnel between the two loopbacks first:"
        echo "  $SMOKING_PI_HOME/docs/remote-openclaw.md"
        echo "Then run this command there, against the tunnelled port."
        echo
        echo "If you do not have OpenClaw at all, nothing is broken: Smoking Pi"
        echo "measures, alerts and draws its dashboards on its own. Install it when"
        echo "you want to ask questions in chat, then run 'smoking-pi openclaw'."
        return 0
    fi

    echo "Connecting this stack to the OpenClaw gateway on this machine."
    local who; who="$(openclaw_user)"
    echo "Using $OPENCLAW_BIN as ${who:-$(id -un)}."
    echo

    # 1. The credential. The MCP server exposes mutations (add/remove
    #    targets, restart SmokePing); Compose binds it to loopback, but
    #    every process on this host is inside that boundary.
    local token; token="$(env_get MCP_API_TOKEN)"
    if [ -z "$token" ]; then
        token="$(openssl rand -hex 32)"
        env_set MCP_API_TOKEN "$token"
        echo "1/5  Generated MCP_API_TOKEN in $ENV_FILE."
    elif [[ "$token" =~ ^[A-Za-z0-9._~-]+$ ]]; then
        echo "1/5  MCP_API_TOKEN is already set; keeping it."
    else
        # The token is interpolated into the JSON handed to `openclaw mcp
        # set`. A generated one is hex, but this one was written by hand or
        # by another tool, and a `"` or `\` in it would produce malformed
        # JSON -- which surfaces as a registration failure that reads like a
        # connectivity problem. Refuse instead of guessing.
        echo "MCP_API_TOKEN in $ENV_FILE contains characters that cannot be" >&2
        echo "passed through safely (anything outside A-Z a-z 0-9 . _ ~ -)." >&2
        echo "Replace it with 'openssl rand -hex 32', restart the server, and" >&2
        echo "re-run: the value is only a bearer token, nothing derives from it." >&2
        return 1
    fi

    # 2. The profile has to be recorded, or the next 'up' drops the server.
    local p; p="$(profiles)"
    case ",$p," in
        *,mcp,*) echo "2/5  The mcp profile is already recorded." ;;
        *) env_set COMPOSE_PROFILES "${p:+$p,}mcp"
           echo "2/5  Added the mcp profile to $ENV_FILE." ;;
    esac
    compose up -d mcp-server >/dev/null 2>&1 || true
    echo "     mcp-server is up."

    # 3. Prove the port is gated before handing its address to anything.
    local code_open code_auth
    code_open="$(curl -s -o /dev/null -w '%{http_code}' -XPOST http://127.0.0.1:8090/mcp 2>/dev/null || echo 000)"
    # The token goes in on stdin (-K -), never in argv: a command line is
    # readable by every account on this host (ps, /proc/*/cmdline). Same
    # reason, and the same shape, as the health checks in
    # shared/scripts/show-passwords.sh. The value MUST be quoted -- curl
    # reads an unquoted `header = A: B` as a key/value line and drops the
    # header in silence, which looks exactly like an auth failure.
    code_auth="$(printf 'url = "%s"\nrequest = "POST"\nsilent\noutput = "/dev/null"\nwrite-out = "%%{http_code}"\nheader = "Authorization: Bearer %s"\n' \
        "http://127.0.0.1:8090/mcp" "$(curl_cfg_quote "$token")" \
        | curl -K - 2>/dev/null || echo 000)"
    if [ "$code_open" = 000 ]; then
        echo "3/5  WARNING: 127.0.0.1:8090 did not answer. Give it a moment and re-run;" >&2
        echo "     'smoking-pi logs mcp-server' says why if it does not come up." >&2
    elif [ "$code_open" = 401 ] && [ "$code_auth" != 401 ]; then
        echo "3/5  The port refuses an unauthenticated request (401) and accepts the token."
    else
        echo "3/5  WARNING: expected 401 without the token and not-401 with it;" >&2
        echo "     got $code_open and $code_auth. The token may not have reached the" >&2
        echo "     container -- 'smoking-pi restart' does not recreate it, try" >&2
        echo "     'smoking-pi down && smoking-pi up'." >&2
    fi

    # 4. Register, then the skill. Registering alone is the documented
    #    half-install: the agent keeps answering from its shell.
    #    The JSON holding the token goes through a 0600 file, not sudo's
    #    command line: sudo logs the full command it runs to the journal.
    #    Timeouts in milliseconds: OpenClaw 2026.8 rejects the retired
    #    connectTimeout/timeout (seconds) and the whole `mcp set` with them.
    local spec user registered=0
    spec="$(mktemp)"
    printf '{\n  "url": "http://127.0.0.1:8090/mcp",\n  "transport": "streamable-http",\n  "headers": {"Authorization": "Bearer %s"},\n  "connectionTimeoutMs": 5000,\n  "requestTimeoutMs": 30000\n}' \
        "$token" > "$spec"
    user="$(openclaw_user)"
    # A failed chown surfaces below as a failed registration, not here.
    [ -z "$user" ] || chown "$user" "$spec" 2>/dev/null || true
    as_openclaw_user sh -c 'exec "$0" mcp set smokeping "$(cat "$1")"' "$OPENCLAW_BIN" "$spec" \
        >/dev/null 2>&1 && registered=1
    # OpenClaw 2026.11 refuses `mcp set` while its gateway runs ("requires
    # exclusive offline state ownership"). It only writes mcp.servers, and a
    # `config set` of the same entry goes through the running gateway.
    [ "$registered" = 1 ] || as_openclaw_user sh -c \
        'exec "$0" config set mcp.servers.smokeping "$(cat "$1")" --strict-json' "$OPENCLAW_BIN" "$spec" \
        >/dev/null 2>&1 && registered=1
    rm -f "$spec"
    if [ "$registered" = 1 ]; then
        echo "4/5  Registered as 'smokeping' with OpenClaw."
    else
        echo "4/5  'openclaw mcp set' failed. Run it by hand from" >&2
        echo "     docs/openclaw-integration.md and re-run this." >&2
        return 1
    fi

    # The hooks before the skill: its --reload restarts the gateway, and that
    # one restart is all both need. Alerts through the agent, so a reply has
    # the context. Not a failure of the connection if they cannot: alerts keep
    # the relay. A person who chose the relay with --push message keeps it.
    local hooks_ok=0
    if [ "$(env_get OPENCLAW_PUSH)" != message ] && openclaw_enable_hooks; then
        hooks_ok=1
    fi

    if [ -x "$SMOKING_PI_HOME/shared/scripts/install-openclaw-skill.sh" ]; then
        local skill_rc=0 skill_said
        echo "5/5  Installing the smokeping-monitoring skill and asking the gateway..."
        skill_said="$(as_openclaw_user "$SMOKING_PI_HOME/shared/scripts/install-openclaw-skill.sh" --reload 2>&1)" \
            || skill_rc=$?
        case "$skill_rc" in
            0) echo "     Installed the skill and reloaded the gateway." ;;
            4) # The script's "restarted, but not answering yet" (#449).
               echo "     Installed the skill and restarted the gateway; it did not answer its"
               echo "     status probe within $(openclaw_said_wait "$skill_said"), so it is still starting and reads"
               echo "     the skill as it does. Give it a moment before the check below." ;;
            3) # The script's "installed, but no gateway answers".
               echo "     Installed the skill, but the gateway did not answer: it is not" >&2
               echo "     running, or refused the status probe, so nothing reloaded." >&2
               echo "     'openclaw gateway status' says which; start it before the check below." >&2 ;;
            *) echo "     WARNING: the skill did not install or the gateway did not reload; run" >&2
               echo "     shared/scripts/install-openclaw-skill.sh --reload as the gateway's user." >&2
               echo "     A user unit needs a session or 'loginctl enable-linger' for systemctl --user." >&2 ;;
        esac
    else
        echo "5/5  WARNING: install-openclaw-skill.sh not found; the agent will answer" >&2
        echo "     from its own shell until the skill is installed." >&2
    fi

    echo
    if [ "$(env_get OPENCLAW_PUSH)" = message ] && [ "$hooks_ok" = 0 ]; then
        echo "Alert push: message relay (your choice; $(cli_name) openclaw --push agent changes it)."
    else
        [ "$hooks_ok" = 0 ] || openclaw_recreate_alerter
        openclaw_report_push
    fi

    # The heartbeat and the weekly. Not a failure of the connection if they
    # cannot be set up: what is missing, it says.
    echo
    if command -v python3 >/dev/null; then
        openclaw_automations_sync "" || true
    else
        echo "Automations: not set up (python3 is needed to read OpenClaw's list)."
    fi

    echo
    echo "A running gateway keeps its cached tool set, so start a NEW chat session"
    echo "before asking anything. Then:"
    echo "  smoking-pi openclaw --check"
    echo "which asks a question and checks the server's log, not the answer."
}
