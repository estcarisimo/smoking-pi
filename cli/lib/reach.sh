# shellcheck shell=bash
# smoking-pi reach: how the LAN finds the stack: DNS-SD (Avahi), `discover`, `url`.
# Sourced by cli/smoking-pi, never run on its own.

# Avahi, which Raspberry Pi OS runs by default, announces what is in
# /etc/avahi/services and reloads the directory by itself. The record says
# a Smoking Pi is here, where its page is and which edition and version it
# runs -- nothing more: anyone on the network can read it.
AVAHI_SERVICE_FILE="${SMOKING_PI_AVAHI_FILE:-/etc/avahi/services/smoking-pi.service}"

avahi_service_xml() {
    local port version grafana=""
    # Only characters XML and a TXT record take as they are: a stray `<` or
    # `&` would make Avahi reject the whole file, silently.
    port="$(ui_port)"; case "$port" in ""|*[!0-9]*) port=8080 ;; esac
    version="$(installed_version | tr -cd 'A-Za-z0-9._+~-')"
    [ "$SMOKING_PI_EDITION" = pro ] && grafana="
    <txt-record>grafana=3000</txt-record>"
    cat <<XML
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<!-- Written by smoking-pi (install, up, upgrade), removed by down and by
     uninstalling the package. Browse with: smoking-pi discover -->
<service-group>
  <name replace-wildcards="yes">Smoking Pi on %h</name>
  <service>
    <type>_smoking-pi._tcp</type>
    <port>$port</port>
    <txt-record>edition=$SMOKING_PI_EDITION</txt-record>
    <txt-record>version=${version:-unknown}</txt-record>
    <txt-record>path=/</txt-record>$grafana
  </service>
  <service>
    <type>_http._tcp</type>
    <port>$port</port>
    <txt-record>path=/</txt-record>
  </service>
</service-group>
XML
}

# Announce the stack on the LAN. Never fatal and never a prompt: no avahi,
# or no right to write its directory (not root), means no record. The
# record is readable by everyone on the network (edition, version, ports);
# SMOKING_PI_ANNOUNCE=0 (environment or /etc/default/smoking-pi) keeps the
# stack off it and withdraws a record already there.
publish_avahi() {
    local dir tmp
    case "${SMOKING_PI_ANNOUNCE:-1}" in 0|no|false|off) unpublish_avahi; return 0 ;; esac
    dir="$(dirname "$AVAHI_SERVICE_FILE")"
    [ -d "$dir" ] && [ -w "$dir" ] || return 0
    tmp="$(mktemp "$dir/.smoking-pi.XXXXXX")" || return 0
    # A failed write keeps the record that is there, never a partial one.
    avahi_service_xml > "$tmp" || { rm -f "$tmp"; return 0; }
    chmod 0644 "$tmp"
    if cmp -s "$tmp" "$AVAHI_SERVICE_FILE"; then rm -f "$tmp"; else mv -f "$tmp" "$AVAHI_SERVICE_FILE"; fi
    return 0
}

unpublish_avahi() {
    [ -w "$(dirname "$AVAHI_SERVICE_FILE")" ] && rm -f "$AVAHI_SERVICE_FILE"
    return 0
}

# Every Smoking Pi announcing itself on this network, from avahi-browse's
# parsable output: =;iface;proto;name;type;domain;host;address;port;"txt"...
cmd_discover() {
    if ! command -v avahi-browse >/dev/null; then
        echo "smoking-pi discover needs avahi-browse: sudo apt install avahi-utils" >&2
        echo "On a Mac: dns-sd -B _smoking-pi._tcp   (then dns-sd -L \"<name>\" _smoking-pi._tcp)" >&2
        return 2
    fi
    local browsed found
    # Its status on its own: under pipefail a failing avahi-browse (no daemon)
    # would end the command here, with nothing said.
    if ! browsed="$(avahi-browse --resolve --terminate --parsable _smoking-pi._tcp 2>/dev/null)"; then
        echo "avahi-browse could not ask the network: is avahi-daemon running here?" >&2
        echo "  systemctl status avahi-daemon" >&2
        return 1
    fi
    # LC_ALL=C: the name's escaped bytes are bytes; in a UTF-8 locale gawk
    # would turn each into a character of its own.
    found="$(printf '%s\n' "$browsed" \
        | LC_ALL=C awk -F';' '
            # avahi escapes a name byte as a three-digit decimal: \032 is a space.
            function unescape(s,   out, i, c) {
                out = ""
                for (i = 1; i <= length(s); i++) {
                    c = substr(s, i, 1)
                    if (c == "\\" && substr(s, i + 1, 3) ~ /^[0-9][0-9][0-9]$/) {
                        out = out sprintf("%c", substr(s, i + 1, 3) + 0); i += 3
                    } else out = out c
                }
                return out
            }
            function txt(key,   i, n, parts) {
                n = split($10, parts, /" "/)
                for (i = 1; i <= n; i++) { gsub(/"/, "", parts[i]); if (index(parts[i], key "=") == 1) return substr(parts[i], length(key) + 2) }
                return ""
            }
            # One entry per name and host, the IPv4 answer when there is one:
            # avahi reports each node once per address family.
            $1 == "=" {
                k = $4 ";" $7
                # The first IPv4 answer stays: a host on eth0 and wlan0 is
                # not reshuffled by the order avahi happens to answer in.
                if (!(k in line)) order[++n] = k
                else if ($3 != "IPv4" || v4[k]) next
                if ($3 == "IPv4") v4[k] = 1
                port = ($9 == 80) ? "" : ":" $9
                line[k] = sprintf("%s\n  http://%s%s/   (%s, %s %s)\n", unescape($4), $7, port, $8, txt("edition"), txt("version"))
            }
            END { for (i = 1; i <= n; i++) printf "%s", line[order[i]] }')"
    if [ -z "$found" ]; then
        echo "No Smoking Pi announces itself on this network."
        echo "A Pi installed before this version announces itself after its next 'smoking-pi up' or upgrade;"
        echo "a network that blocks multicast (guest Wi-Fi, client isolation) hides it."
        return 1
    fi
    printf '%s\n' "$found"
}

# The .local name the mdns service holds (docs/mdns.md); empty when it is
# off, not running, or still probing.
mdns_name() {
    local body
    body="$(compose exec -T mdns python status.py --json 2>/dev/null)" || return 0
    printf '%s\n' "$body" | grep -q '"state": "announced"' || return 0
    printf '%s\n' "$body" | sed -n 's/^ *"name": "\([a-z0-9.-]*\)",\{0,1\}$/\1/p' | head -n 1
}

cmd_url() {
    # What to open, where, and with which username. Printed at the end of
    # `install` and on demand: the single most useful thing an install can
    # say, and the one it used to say as "http://localhost:8080" -- which,
    # to someone who installed over SSH from a laptop, is the laptop.
    local wait=0
    while [ $# -gt 0 ]; do
        case "$1" in
            --wait) wait="${2:-}"; shift $(( $# > 1 ? 2 : 1 )) ;;
            *) echo "unknown option $1" >&2; exit 2 ;;
        esac
    done
    case "$wait" in ""|*[!0-9]*) echo "usage: smoking-pi url [--wait SECONDS]" >&2; exit 2 ;; esac
    local host client port url label login
    host="$(host_address)"; client="$(ssh_client)"
    # The first URL is the one a person customizes Smoking Pi from; Basic
    # has no web admin, so it is SmokePing's own page. Ports as each
    # edition's compose file maps them: Pro's web admin and Grafana are
    # fixed there, the others read the env file.
    port="$(ui_port)"
    case "$SMOKING_PI_EDITION" in
        basic) label="SmokePing"; login="no login" ;;
        *) label="Web admin"; login="user $(env_value WEB_ADMIN_USERNAME | grep . || echo admin)" ;;
    esac
    url="http://$host$([ "$port" = 80 ] || echo ":$port")/"
    if [ "$wait" -gt 0 ]; then
        # The first `up` builds or pulls images and the web admin waits for
        # PostgreSQL; a URL printed before it answers is a URL that fails
        # the first time it is tried.
        local i=0
        until http_answers "$url" || [ "$i" -ge "$wait" ]; do sleep 3; i=$((i + 3)); done
    fi
    echo "Open Smoking Pi:"
    printf '  %-10s %s   (%s)\n' "$label" "$url" "$login"
    if [ "$SMOKING_PI_EDITION" = pro ]; then
        printf '  %-10s %s   (user %s)\n' "Grafana" "http://$host:3000/" "$(env_value GF_SECURITY_ADMIN_USER | grep . || echo admin)"
    fi
    if [ "$SMOKING_PI_EDITION" != basic ]; then
        echo "  Passwords: $(cli_name) passwords --show-secrets (on this machine)"
    fi
    if [ -n "$client" ]; then
        echo "Open it on the computer you are connected from ($client), not in this terminal."
    elif [ "$host" != localhost ]; then
        echo "Open it on this machine or any computer on the same network."
    fi
    # A .local name survives a DHCP lease changing the address, which the
    # IP above does not. The mdns service's name first: it says which name
    # it holds. `hostname`.local is only a guess -- Avahi renames itself to
    # <hostname>-2 on a conflict, and the reference Pi did, twice.
    local name; name="$(mdns_name)"
    if [ -z "$name" ] && command -v systemctl >/dev/null && systemctl is-active --quiet avahi-daemon 2>/dev/null; then
        name="$(hostname -s).local"
    fi
    if [ -n "$name" ]; then
        echo "Also, from most computers on this network: http://$name$([ "$port" = 80 ] || echo ":$port")/"
    fi
    if ! http_answers "$url"; then
        echo
        echo "Nothing answers at $url yet. The first start can take minutes;"
        echo "'$(cli_name) status' shows what is up, '$(cli_name) logs' why not."
        return 1
    fi
    if [ -n "$client" ]; then
        echo "Not loading there? A firewall between you and this machine; tunnel through SSH:"
        # ssh takes a bare v6 address after the @, not the URL's brackets.
        local h="${host#[}"; h="${h%]}"
        echo "  ssh -L $port:localhost:$port ${SUDO_USER:-$USER}@$h   then open http://localhost:$port/"
    fi
}

# How to name this command in advice: a package install keeps its env file
# root-only, so every command that reads it needs sudo.
