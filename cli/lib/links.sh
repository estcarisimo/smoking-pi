# shellcheck shell=bash
# smoking-pi links: `links`, where alert and assistant links point.
# Sourced by cli/smoking-pi, never run on its own.

lan_address() {
    # The address the LAN sees: the default IPv4 route's source. Not
    # host_address, which over SSH prefers the address the client used --
    # a tailnet address, say, that a phone on the Wi-Fi cannot open.
    ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -1
}

ipv6_base() {
    # An IPv6 literal for PUBLIC_BASE_HOST, stored bracketed ([2001:db8::5],
    # [2001:db8::5]:9999) so nobody reading the env file has to guess
    # whether the last group is a port. common/links.py accepts both forms.
    local v="$1" addr port=""
    case "$v" in
        \[*\]) addr="${v#[}"; addr="${addr%]}" ;;
        \[*\]:*) addr="${v#[}"; addr="${addr%%]*}"; port="${v##*]:}"
                 case "$port" in ""|*[!0-9]*) echo "$v: expected [address]:port" >&2; return 1 ;; esac ;;
        \[*) echo "$v: expected [address] or [address]:port" >&2; return 1 ;;
        *) addr="$v" ;;
    esac
    addr="$(printf '%s' "$addr" | tr 'A-F' 'a-f')"
    case "$addr" in
        # Link-local routes only with a zone id (fe80::1%wlan0), and
        # browsers refuse zone ids in a URL: every link would be dead.
        *%*|fe[89ab][0-9a-f]:*)
            echo "$v is link-local or names a zone (%...): a browser cannot open it. Give a global or ULA address, a hostname, or an IPv4 address." >&2; return 1 ;;
        ::|0:0:0:0:0:0:0:0) echo "$v is no host's address; give this machine's own." >&2; return 1 ;;
        ::1|0:0:0:0:0:0:0:1) echo "$v only opens on this machine; a link in an alert is read on a phone." >&2; return 1 ;;
        *[!0-9a-f:.]*|*:::*|*[!:]:|:[!:]*) echo "$v is not an IPv6 address." >&2; return 1 ;;
        *:*:*) ;;
        *) echo "$v is not an IPv6 address." >&2; return 1 ;;
    esac
    # Grafana publishes "3000:3000", which Docker binds on IPv4 and IPv6;
    # the web admin publishes 0.0.0.0:8080, IPv4 only.
    if grep -q '"0\.0\.0\.0:8080:8080"' "$EDITION_DIR/docker-compose.yml"; then
        echo "Note: the web admin listens on IPv4 only, so its links in alerts will not open over IPv6; Grafana's will." >&2
    fi
    echo "[$addr]${port:+:$port}"
}

cmd_links() {
    local lan="" tunnel="" off=0 changed_lan=0 changed_tunnel=0
    while [ $# -gt 0 ]; do
        case "$1" in
            # A value that looks like an option is a forgotten value:
            # `--lan --off` would otherwise store "--off" as the address.
            --lan) case "${2:-}" in ""|-*) echo "--lan needs auto, mdns or an address" >&2; return 2 ;; esac
                   lan="$2"; shift 2 ;;
            --tunnel) case "${2:-}" in ""|-*) echo "--tunnel needs a URL" >&2; return 2 ;; esac
                      tunnel="$2"; shift 2 ;;
            --off) off=1; shift ;;
            -h|--help) usage; return 0 ;;
            *) echo "unknown option $1" >&2; return 2 ;;
        esac
    done
    need_edition
    if ! grep -q '^  mcp-server:' "$EDITION_DIR/docker-compose.yml"; then
        echo "Links are made by the alerter and the MCP server, Pro services; the $SMOKING_PI_EDITION edition has neither." >&2
        return 1
    fi
    if [ "$off" = 1 ]; then
        env_set PUBLIC_BASE_HOST ""; env_set TUNNEL_BASE_HOST ""
        changed_lan=1; changed_tunnel=1
        echo "Links off: alerts and answers carry no links (none beats a broken one)."
    fi
    if [ -n "$lan" ]; then
        if [ "$lan" = auto ]; then
            lan="$(lan_address)"
            [ -n "$lan" ] || { echo "No IPv4 default route here; give the address: --lan 192.168.1.20" >&2; return 1; }
        fi
        if [ "$lan" = mdns ]; then
            # The name it holds now, not MDNS_NAME: after a conflict it is
            # smoking-pi-2.local, and a link to the other host's name opens
            # the other host.
            lan="$(mdns_name)"
            [ -n "$lan" ] || { echo "The mdns service holds no name (off, not running, or still probing): smoking-pi url says which. Or give the address: --lan auto" >&2; return 1; }
            echo "Links use $lan. Only a device that resolves .local names (multicast DNS) can open them; check on the phone that reads the alerts, or use --lan auto, the address any device can open." >&2
        fi
        case "$lan" in
            # scheme://host is what a reverse proxy on the LAN needs: no
            # port is added then (common/links.py). A path is not a base.
            http://*/*|https://*/*) echo "--lan takes a host or scheme://host, without a path." >&2; return 2 ;;
            http://*|https://*) ;;
            *://*|*/*) echo "--lan takes a host, an address, or http(s)://host." >&2; return 2 ;;
            \[*|*:*:*) lan="$(ipv6_base "$lan")" || return 2 ;;
        esac
        case "${lan#*://}" in
            127.*|localhost|localhost:*) echo "$lan only opens on this machine; a link in an alert is read on a phone." >&2; return 2 ;;
        esac
        env_set PUBLIC_BASE_HOST "$lan"; changed_lan=1
    fi
    if [ -n "$tunnel" ]; then
        # A bare host gets http:// and the service port appended: right for a
        # LAN address, wrong for every tunnel, which answers https on 443 --
        # a dead link that looks fine (.env.template says so too).
        case "$tunnel" in
            https://*|http://*) ;;
            *) echo "Include the scheme: --tunnel https://$tunnel (a bare host becomes http://$tunnel:3000, a dead link)." >&2; return 2 ;;
        esac
        env_set TUNNEL_BASE_HOST "${tunnel%/}"; changed_tunnel=1
    fi
    local pub tun
    pub="$(env_get PUBLIC_BASE_HOST)"; tun="$(env_get TUNNEL_BASE_HOST)"
    echo "Links in alerts and assistant answers:"
    if [ -n "$pub" ]; then
        case "$pub" in
            *://*) echo "  at home:       $pub" ;;
            # A host that names its own port serves both services on it.
            \[*\]:*) echo "  at home:       http://$pub/" ;;
            \[*\]) echo "  at home:       http://$pub:3000/ (Grafana), http://$pub:8080/ (web admin)" ;;
            *:*:*) echo "  at home:       http://[$pub]:3000/ (Grafana), http://[$pub]:8080/ (web admin)" ;;
            *:*) echo "  at home:       http://$pub/" ;;
            *) echo "  at home:       http://$pub:3000/ (Grafana), http://$pub:8080/ (web admin)" ;;
        esac
    else
        echo "  at home:       none (smoking-pi links --lan auto)"
    fi
    case "$pub" in
        *.local|*.local:*|*://*.local|*://*.local:*)
            local held; held="$(mdns_name)"
            if [ -n "$held" ] && [ "$held" != "$(printf '%s' "${pub#*://}" | sed 's/:[0-9]*$//')" ]; then
                echo "  Warning: the mdns service now holds $held, not this name; links may open another host. Run: smoking-pi links --lan mdns" >&2
            fi ;;
    esac
    if [ -n "$tun" ]; then
        echo "  from anywhere: $tun"
    else
        echo "  from anywhere: none (--tunnel https://..., see docs/cloudflare-tunnel-setup.md)"
    fi
    if [ -n "$(env_get GRAFANA_PUBLIC_URL)$(env_get WEB_ADMIN_PUBLIC_URL)$(env_get GRAFANA_TUNNEL_URL)$(env_get WEB_ADMIN_TUNNEL_URL)" ]; then
        echo "  (per-service URLs are set too, and win over these: smoking-pi config list)"
    fi
    # Each changed key applies its own readers (the same two services today,
    # which is the compose file's business, not this function's).
    if [ "$changed_lan" = 1 ]; then config_apply PUBLIC_BASE_HOST; fi
    if [ "$changed_tunnel" = 1 ] && [ "$changed_lan" = 0 ]; then config_apply TUNNEL_BASE_HOST; fi
}
