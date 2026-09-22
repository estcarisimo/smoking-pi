#!/bin/bash

# SmokePing Full Stack - Enhanced Password Display Script
# Shows all credentials, access information, and system status

# Colors for better readability
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m' # No Color

usage() {
    cat <<'EOF'
Usage: show-passwords.sh [--show-secrets [--force]]

Shows this edition's service URLs, usernames, health checks and quick
commands. Secret VALUES are hidden unless you ask for them.

  -s, --show-secrets  Print the secret values themselves.
      --force         With --show-secrets, print them even when the output
                      is not a terminal (a pipe, a file, a log, an
                      assistant capturing the run). Refused without it.
  -h, --help          This text.

Whether a secret is set at all is always reported: an unset token means an
unauthenticated endpoint, which you need to know either way.
EOF
}

SHOW_SECRETS=0
FORCE=0
while [ $# -gt 0 ]; do
    case "$1" in
        -s|--show-secrets) SHOW_SECRETS=1; shift ;;
        --force) FORCE=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown option $1" >&2; usage >&2; exit 2 ;;
    esac
done

# Asking for the values is explicit; sending them somewhere that keeps them
# is not. A redirect, a pipe or an assistant reading the run all outlive the
# terminal, so --show-secrets stops there unless --force says to go on.
if [ "$SHOW_SECRETS" = 1 ] && [ "$FORCE" = 0 ] && [ ! -t 1 ]; then
    echo "refusing to print secrets: stdout is not a terminal." >&2
    echo "This output would outlive the screen -- a file, a pipe, a log, a" >&2
    echo "transcript. Add --force if that is what you meant." >&2
    exit 3
fi

# curl reads these options from stdin (-K -) instead of argv, because a
# credential on a command line is readable by every account on the host
# (ps, /proc/*/cmdline) however the output is gated. The value MUST be
# quoted: curl's config parser treats an unquoted `header = A: B` as a
# key/value line and drops the header silently, which looks exactly like an
# authentication failure. Inside quotes it honors \\ and \", so escape both.
curl_quote() { printf '%s' "$1" | sed 's/[\\"]/\\&/g'; }

# A secret's value, or the fact that it has one. An EMPTY secret is never
# withheld: "unset" is a warning (an unauthenticated API, a database with no
# password), not a credential.
secret() {
    if [ -z "$1" ]; then
        printf '%b' "${RED}unset${NC}"
    elif [ "$SHOW_SECRETS" = 1 ]; then
        printf '%b%s%b' "$YELLOW" "$1" "$NC"
    else
        printf '%b' "${GREEN}set${NC} ${CYAN}(hidden)${NC}"
    fi
}

# Detect SmokePing edition
detect_edition() {
    # Check if docker-compose.yml exists in current directory
    if [ -f "docker-compose.yml" ]; then
        if grep -q "grafana" docker-compose.yml && grep -q "influxdb\|clickhouse" docker-compose.yml; then
            echo "pro"
        elif grep -q "postgres" docker-compose.yml && grep -q "config-manager" docker-compose.yml; then
            echo "standard"
        elif grep -q "smokeping" docker-compose.yml; then
            echo "basic"
        else
            echo "unknown"
        fi
    else
        echo "unknown"
    fi
}

EDITION=$(detect_edition)
# The Compose command for this edition: the env file may be relocated
# (--env-file), and Compose v1 (`docker-compose`) cannot read these files.
compose() { docker compose --env-file "${SMOKING_PI_ENV_FILE:-.env}" "$@"; }

echo -e "${CYAN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║${WHITE}    🔑 SmokePing ${EDITION^} Edition Credentials    ${CYAN}║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════╝${NC}"
if [ "$SHOW_SECRETS" = 1 ]; then
    echo -e "${YELLOW}Showing secret values. Mind the screen, the scrollback and any recording.${NC}"
else
    echo -e "Secret values are hidden. To show them: ${CYAN}smoking-pi passwords --show-secrets${NC}"
fi
echo

# The env file is ./.env unless relocated (docs/packaging.md, "Relocatable
# state"); the smoking-pi command and the systemd unit set the variable.
ENV_FILE="${SMOKING_PI_ENV_FILE:-.env}"

# Check if the env file exists
if [ ! -f "$ENV_FILE" ]; then
    echo -e "${RED}❌ Error: $ENV_FILE not found!${NC}"
    echo -e "${YELLOW}The system will auto-generate passwords on first run.${NC}"
    echo -e "${GREEN}Just run: ./setup.sh (or: sudo smoking-pi install)${NC}"
    echo
    
    # Passwords left behind by a zero-touch deployment -- a plain wall of
    # secrets, so it needs the same asking-for-it as every other value.
    if [ -f ".passwords-generated" ]; then
        if [ "$SHOW_SECRETS" = 1 ]; then
            echo -e "${GREEN}✅ Found generated passwords from initial deployment:${NC}"
            echo
            cat .passwords-generated
            rm -f .passwords-generated  # Remove after showing
        else
            echo -e "${GREEN}✅ Found generated passwords from initial deployment.${NC}"
            echo -e "   Show them (once -- the file is deleted after): ${CYAN}$0 --show-secrets${NC}"
        fi
    fi
    exit 1
fi

# Load environment variables
source "$ENV_FILE"
# The ports as the compose files map them: SmokePing on SMOKEPING_PORT
# (Basic's default 80; Standard's 8081; Pro's SmokePing runs on the host
# network, port 80), the web admin on WEB_ADMIN_PORT (8080).
case "$EDITION" in
    basic) SMOKEPING_URL_PORT="${SMOKEPING_PORT:-80}" ;;
    standard) SMOKEPING_URL_PORT="${SMOKEPING_PORT:-8081}" ;;
    *) SMOKEPING_URL_PORT=80 ;;
esac
WEB_ADMIN_URL_PORT="${WEB_ADMIN_PORT:-8080}"

# Detect time-series database type for pro edition
TSDB_TYPE=${TSDB_TYPE:-influxdb}

# Compose names a volume "<project>_<key>", and the project is
# COMPOSE_PROJECT_NAME or, failing that, the directory this runs in --
# never the "grafana-influx" this script used to print, which has not been
# the project name since the editions split.
PROJECT="${COMPOSE_PROJECT_NAME:-$(basename "$PWD")}"

# Every secret lives in one file, so hiding them on screen means little if
# any account on the host can read it. GNU stat is Linux; -f '%Lp' is BSD's.
ENV_PERMS=$(stat -c '%a' "$ENV_FILE" 2>/dev/null || stat -f '%Lp' "$ENV_FILE" 2>/dev/null || echo "")
if [ -n "$ENV_PERMS" ] && [ "${ENV_PERMS: -2}" != "00" ]; then
    echo -e "${YELLOW}⚠️  $ENV_FILE is mode $ENV_PERMS: readable beyond its owner.${NC}"
    echo -e "   Every secret below is in it. Tighten with: ${CYAN}chmod 600 $ENV_FILE${NC}"
    echo
fi

# Get server IP addresses
SERVER_IP=$(hostname -I | awk '{print $1}')
EXTERNAL_IP=$(curl -s -m 2 ifconfig.me 2>/dev/null || echo "Unable to detect")

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${WHITE}📊 Service Access URLs${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}Local Access:${NC}"

# Show services based on edition
case "$EDITION" in
    "basic")
        echo -e "  SmokePing:   http://localhost:${SMOKEPING_URL_PORT}"
        ;;
    "standard")
        echo -e "  SmokePing:   http://localhost:${SMOKEPING_URL_PORT}"
        echo -e "  Web Admin:   http://localhost:${WEB_ADMIN_URL_PORT}"
        ;;
    "pro")
        echo -e "  SmokePing:   http://localhost:${SMOKEPING_URL_PORT}"
        echo -e "  Web Admin:   http://localhost:${WEB_ADMIN_URL_PORT}"
        echo -e "  Grafana:     http://localhost:3000"
        if [ "$TSDB_TYPE" = "clickhouse" ]; then
            echo -e "  ClickHouse:  http://localhost:8123"
        else
            echo -e "  InfluxDB:    http://localhost:8086"
        fi
        ;;
    *)
        echo -e "  ${YELLOW}Unknown edition - please check docker-compose.yml${NC}"
        ;;
esac

echo
echo -e "${GREEN}Network Access (from other devices):${NC}"

case "$EDITION" in
    "basic")
        echo -e "  SmokePing:   http://${SERVER_IP}:${SMOKEPING_URL_PORT}"
        ;;
    "standard")
        echo -e "  SmokePing:   http://${SERVER_IP}:${SMOKEPING_URL_PORT}"
        echo -e "  Web Admin:   http://${SERVER_IP}:${WEB_ADMIN_URL_PORT}"
        ;;
    "pro")
        echo -e "  SmokePing:   http://${SERVER_IP}:${SMOKEPING_URL_PORT}"
        echo -e "  Web Admin:   http://${SERVER_IP}:${WEB_ADMIN_URL_PORT}"
        echo -e "  Grafana:     http://${SERVER_IP}:3000"
        if [ "$TSDB_TYPE" = "clickhouse" ]; then
            echo -e "  ClickHouse:  http://${SERVER_IP}:8123"
        else
            echo -e "  InfluxDB:    http://${SERVER_IP}:8086"
        fi
        ;;
esac

if [ "$EXTERNAL_IP" != "Unable to detect" ] && [ "$EDITION" != "basic" ]; then
    echo
    echo -e "${GREEN}External Access (if port forwarding enabled):${NC}"
    case "$EDITION" in
        "standard"|"pro")
            echo -e "  Web Admin:   http://${EXTERNAL_IP}:${WEB_ADMIN_URL_PORT}"
            ;;
    esac
fi


# Show Grafana credentials only for Pro edition
if [ "$EDITION" = "pro" ]; then
    echo
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}🌐 Grafana Credentials${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  ${PURPLE}URL:${NC}          http://localhost:3000"
echo -e "  ${PURPLE}Username:${NC}     ${GF_SECURITY_ADMIN_USER:-admin}"
if [ -n "$GF_SECURITY_ADMIN_PASSWORD" ]; then
    echo -e "  ${PURPLE}Password:${NC}     $(secret "$GF_SECURITY_ADMIN_PASSWORD")"
else
    echo -e "  ${PURPLE}Password:${NC}     admin ${YELLOW}(default - change on first login!)${NC}"
    echo -e "  ${RED}⚠️  No GF_SECURITY_ADMIN_PASSWORD in $ENV_FILE${NC}"
    echo -e "     Set one (openssl rand -base64 24) and recreate Grafana."
fi
# Whether Grafana is up is worth saying whichever way the password went.
if compose ps --status running --services 2>/dev/null | grep -qx grafana; then
    echo -e "  ${GREEN}✅ Grafana is running${NC}"
else
    echo -e "  ${RED}❌ Grafana is not running${NC}"
    echo -e "     ${YELLOW}Run: docker compose up -d grafana${NC}"
fi
echo
echo -e "  ${CYAN}Troubleshooting Grafana Login:${NC}"
echo -e "  • Grafana applies GF_SECURITY_ADMIN_PASSWORD only when it first"
echo -e "    initializes its database. On a volume that already exists, the"
echo -e "    env file and the login can disagree -- reset the account itself:"
echo -e "    ${YELLOW}docker compose exec -T grafana \\${NC}"
echo -e "    ${YELLOW}  grafana cli admin reset-admin-password --password-from-stdin${NC}"
echo -e "    (type the new password on stdin, then put it in $ENV_FILE)"
echo -e "  • Do NOT delete ${PROJECT}_grafana-data to fix a login: it holds every"
echo -e "    dashboard, annotation and user you have added since install."

fi

# API tokens (Standard and Pro). These are what a script or an MCP client
# needs; the web-admin sends the config-manager one on its own.
if [ "$EDITION" = "standard" ] || [ "$EDITION" = "pro" ]; then
    echo
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}🔑 API Tokens${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    if [ -n "$CONFIG_API_TOKEN" ]; then
        echo -e "  ${PURPLE}config-manager:${NC} $(secret "$CONFIG_API_TOKEN")"
        echo -e "     Authorization: Bearer <token>  on http://127.0.0.1:5000 (except /health)"
    else
        echo -e "  ${PURPLE}config-manager:${NC} ${RED}unset -- the API is unauthenticated${NC}"
        echo -e "     Set CONFIG_API_TOKEN in $ENV_FILE (openssl rand -hex 32) and restart"
    fi
    if [ "$EDITION" = "pro" ]; then
        if [ -n "$MCP_API_TOKEN" ]; then
            echo -e "  ${PURPLE}MCP server:${NC}     $(secret "$MCP_API_TOKEN")"
            echo -e "     Bearer token for http://127.0.0.1:8090/mcp (profile: mcp)"
        else
            echo -e "  ${PURPLE}MCP server:${NC}     ${RED}unset -- the MCP endpoint is unauthenticated${NC}"
            echo -e "     Set MCP_API_TOKEN in $ENV_FILE (openssl rand -hex 32) and restart"
        fi
    fi
fi

# Show time-series database credentials for Pro edition
if [ "$EDITION" = "pro" ]; then
    echo
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    
    if [ "$TSDB_TYPE" = "clickhouse" ]; then
        echo -e "${WHITE}💾 ClickHouse Database${NC}"
        echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "  ${PURPLE}URL:${NC}          http://localhost:8123"
        echo -e "  ${PURPLE}Database:${NC}     ${CLICKHOUSE_DB:-smokeping}"
        echo -e "  ${PURPLE}Username:${NC}     ${CLICKHOUSE_USER:-smokeping}"
        echo -e "  ${PURPLE}Password:${NC}     $(secret "$CLICKHOUSE_PASSWORD")"
        [ -n "$CLICKHOUSE_PASSWORD" ] || echo -e "     ${RED}Not set in $ENV_FILE${NC}"
        
        # Test ClickHouse connectivity
        if curl -s "http://localhost:8123/ping" >/dev/null 2>&1; then
            echo -e "  ${GREEN}✅ ClickHouse server is responding${NC}"
            
            # Test database connection with credentials
            if printf 'user = "%s:%s"\n' \
                   "$(curl_quote "${CLICKHOUSE_USER:-smokeping}")" \
                   "$(curl_quote "$CLICKHOUSE_PASSWORD")" |
               curl -sf -K - "http://localhost:8123/" --data "SELECT 1" >/dev/null 2>&1; then
                echo -e "  ${GREEN}✅ ClickHouse authentication works${NC}"
            else
                echo -e "  ${RED}❌ ClickHouse authentication failed${NC}"
                echo -e "     ${YELLOW}⚠️  Grafana dashboards may show connection errors${NC}"
            fi
        else
            echo -e "  ${RED}❌ ClickHouse server is not responding${NC}"
            echo -e "     ${YELLOW}⚠️  Check if ClickHouse container is running${NC}"
            echo -e "     ${CYAN}🛠️  QUICK FIX:${NC}"
            echo -e "     ${YELLOW}docker compose -f docker-compose.yml -f docker-compose.clickhouse.yml up -d${NC}"
        fi
    else
        echo -e "${WHITE}💾 InfluxDB Database${NC}"
        echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "  ${PURPLE}URL:${NC}          http://localhost:8086"
        echo -e "  ${PURPLE}Organization:${NC} ${INFLUX_ORG}"
        echo -e "  ${PURPLE}Bucket:${NC}       ${INFLUX_BUCKET}"
        echo -e "  ${PURPLE}Admin User:${NC}   admin"
        echo -e "  ${PURPLE}Admin Pass:${NC}   $(secret "$DOCKER_INFLUXDB_INIT_PASSWORD")"
        [ -n "$DOCKER_INFLUXDB_INIT_PASSWORD" ] || echo -e "     ${RED}Not set in $ENV_FILE${NC}"
        if [ -n "$INFLUX_TOKEN" ]; then
            echo -e "  ${PURPLE}API Token:${NC}    $(secret "$INFLUX_TOKEN")"
            
            # Test InfluxDB connectivity
            if printf 'header = "Authorization: Token %s"\n' "$(curl_quote "$INFLUX_TOKEN")" |
               curl -sf -K - "http://localhost:8086/api/v2/buckets?org=$INFLUX_ORG" >/dev/null 2>&1; then
                echo -e "  ${GREEN}✅ InfluxDB token is valid${NC}"
            else
                echo -e "  ${RED}❌ InfluxDB token authentication failed${NC}"
                echo -e "     ${YELLOW}⚠️  Grafana dashboards will show 'unauthorized access' errors${NC}"
                echo ""
                echo -e "     ${CYAN}🛠️  FIX (keeps your measurements):${NC}"
                echo -e "     ${YELLOW}./sync-influx-token.sh${NC}"
                echo -e "     re-reads the token from the running InfluxDB and writes"
                echo -e "     it back to $ENV_FILE, then restarts what uses it."
                echo -e "     ${RED}Deleting ${PROJECT}_influxdb-data would fix it too -- by${NC}"
                echo -e "     ${RED}erasing every measurement ever recorded. Back up first:${NC}"
                echo -e "     ${YELLOW}smoking-pi backup${NC}"
            fi
        else
            echo -e "  ${PURPLE}API Token:${NC}    $(secret "")"
            echo -e "     ${RED}Not set in $ENV_FILE${NC}"
        fi
    fi

fi

# Show PostgreSQL credentials for Standard and Pro editions
if [ "$EDITION" = "standard" ] || [ "$EDITION" = "pro" ]; then
    echo
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}🗄️ PostgreSQL Database${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
if [ -n "$POSTGRES_PASSWORD" ]; then
    echo -e "  ${PURPLE}Database:${NC}     smokeping_targets"
    echo -e "  ${PURPLE}Username:${NC}     smokeping"
    echo -e "  ${PURPLE}Password:${NC}     $(secret "$POSTGRES_PASSWORD")"
    echo -e "  ${PURPLE}Host:${NC}         localhost:5432 (container: postgres:5432)"

    # Through Compose, never by a guessed container name: the old
    # `grafana-influx_postgres_1` stopped existing when the editions split,
    # so this check reported "connection failed" on every healthy stack.
    if compose exec -T postgres psql -U smokeping -d smokeping_targets -c "SELECT 1;" >/dev/null 2>&1; then
        # Check if database has targets
        TARGET_COUNT=$(compose exec -T postgres psql -U smokeping -d smokeping_targets -t -c "SELECT COUNT(*) FROM targets;" 2>/dev/null | xargs)
        if [ -n "$TARGET_COUNT" ] && [ "$TARGET_COUNT" -gt 0 ]; then
            echo -e "  ${GREEN}✅ PostgreSQL connection works ($TARGET_COUNT targets found)${NC}"
        else
            echo -e "  ${YELLOW}⚠️  PostgreSQL works but database is empty (0 targets)${NC}"
            echo -e "     ${YELLOW}Config-manager may be in YAML fallback mode${NC}"
            echo ""
            # verify-postgres.sh ships with Pro only; this block also runs for Standard.
        if [ "$EDITION" = "pro" ]; then
            echo -e "     ${CYAN}🔍 For diagnosis:${NC} ${YELLOW}./verify-postgres.sh${NC}"
        fi
        fi
    else
        echo -e "  ${RED}❌ PostgreSQL connection failed${NC}"
        echo -e "     ${YELLOW}⚠️  Config-manager will use YAML fallback mode${NC}"
        echo -e "     ${YELLOW}⚠️  Grafana template variables will fail${NC}"
        echo ""
        echo -e "     ${CYAN}🛠️  START HERE:${NC}"
        echo -e "     ${YELLOW}smoking-pi logs postgres${NC}   then   ${YELLOW}smoking-pi restart postgres${NC}"
        echo -e "     ${RED}Do not reach for 'docker volume rm ${PROJECT}_postgres-data':${NC}"
        echo -e "     ${RED}that is your targets, categories and sources, not a cache.${NC}"
        echo ""
        if [ "$EDITION" = "pro" ]; then
            echo -e "     ${CYAN}🔍 For detailed diagnosis:${NC} ${YELLOW}./verify-postgres.sh${NC}"
        fi
    fi
    
    # DATABASE_URL embeds the password.
    if [ -n "$DATABASE_URL" ]; then
        echo -e "  ${PURPLE}URL:${NC}          $(secret "$DATABASE_URL")"
    fi
else
    echo -e "  ${RED}PostgreSQL:   Not configured in $ENV_FILE${NC}"
fi

fi

# Show Web Admin credentials for Standard and Pro editions
if [ "$EDITION" = "standard" ] || [ "$EDITION" = "pro" ]; then
    echo
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}🔧 Web Admin Interface${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  ${PURPLE}Login URL:${NC}    http://localhost:8080/auth/login"
if [ -n "$WEB_ADMIN_USERNAME" ]; then
    echo -e "  ${PURPLE}Username:${NC}     ${WEB_ADMIN_USERNAME}"
else
    echo -e "  ${PURPLE}Username:${NC}     admin ${YELLOW}(default)${NC}"
fi
echo -e "  ${PURPLE}Password:${NC}     $(secret "$WEB_ADMIN_PASSWORD")"
[ -n "$WEB_ADMIN_PASSWORD" ] || echo -e "     ${RED}Not set in $ENV_FILE${NC}"
echo -e "  ${PURPLE}Secret Key:${NC}   $(secret "$SECRET_KEY")"
[ -n "$SECRET_KEY" ] || echo -e "     ${RED}Not set in $ENV_FILE -- sessions will not survive a restart${NC}"

fi

echo
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${WHITE}🌍 System Configuration${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  ${PURPLE}Timezone:${NC}     ${TZ:-UTC}"

# Show additional config based on edition
if [ "$EDITION" = "pro" ]; then
    if [ "$TSDB_TYPE" = "clickhouse" ]; then
        echo -e "  ${PURPLE}ClickHouse URL:${NC} ${CLICKHOUSE_URL:-http://clickhouse:8123}"
        echo -e "  ${PURPLE}Database Type:${NC}  ClickHouse"
    else
        echo -e "  ${PURPLE}InfluxDB URL:${NC} ${INFLUX_URL:-http://influxdb:8086}"
        echo -e "  ${PURPLE}Database Type:${NC}  InfluxDB"
    fi
    echo -e "  ${PURPLE}RRD Dir:${NC}      ${RRD_DIR}"
fi

# Check if this was auto-generated
if grep -q "Auto-generated by zero-touch deployment" "$ENV_FILE" 2>/dev/null; then
    GENERATION_DATE=$(grep "Generated:" "$ENV_FILE" | sed 's/# Generated: //')
    echo -e "  ${PURPLE}Generated:${NC}    ${GENERATION_DATE}"
fi

echo
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${WHITE}🚀 Service Status${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

if [ -f "docker-compose.yml" ]; then
    # What Compose says about each service this edition runs: state and,
    # for one that stopped, its exit code. Compose v2 only -- the compose
    # files use v2 semantics, so v1 could not read them anyway.
    check_service() {
        local service="$1" state
        state=$(compose ps -a --format '{{.Service}} {{.State}} {{.ExitCode}}' 2>/dev/null | awk -v s="$service" '$1 == s { print $2, $3; exit }')
        case "$state" in
            running*) echo -e "  ${GREEN}✅ $service is running${NC}" ;;
            exited*) echo -e "  ${RED}❌ $service exited (code: ${state#exited })${NC}" ;;
            "") echo -e "  ${YELLOW}⚠️  $service not found${NC}" ;;
            *) echo -e "  ${YELLOW}⚠️  $service is ${state%% *}${NC}" ;;
        esac
    }
    check_service "smokeping"
    case "$EDITION" in
        "standard")
            check_service "postgres"
            check_service "config-manager"
            check_service "web-admin"
            ;;
        "pro")
            check_service "postgres"
            check_service "config-manager"
            check_service "web-admin"
            if [ "$TSDB_TYPE" = "clickhouse" ]; then
                check_service "clickhouse"
            else
                check_service "influxdb"
            fi
            check_service "grafana"
            ;;
    esac
else
    echo -e "  ${RED}docker-compose.yml not found: run this from an edition directory${NC}"
fi

echo
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${WHITE}📋 Quick Commands${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
# The smoking-pi command (packaging/smoking-pi from a clone, /usr/bin from
# the package) is the documented way; the compose lines are what it runs.
echo -e "  ${CYAN}View logs:${NC}        smoking-pi logs [service]      (docker compose logs -f [service])"
echo -e "  ${CYAN}Check status:${NC}     smoking-pi status              (docker compose ps)"
echo -e "  ${CYAN}Restart:${NC}          smoking-pi restart             (docker compose restart [service])"
echo -e "  ${CYAN}Stop all:${NC}         smoking-pi down                (docker compose down)"
echo -e "  ${CYAN}Upgrade:${NC}          smoking-pi upgrade             (pull or rebuild, up -d, doctor)"
echo -e "  ${CYAN}Backup:${NC}           smoking-pi backup [DIR]"
echo -e "  ${CYAN}Show secrets:${NC}     smoking-pi passwords --show-secrets"
echo
echo -e "${YELLOW}PostgreSQL Commands:${NC}"
echo -e "  ${CYAN}Database status:${NC}  curl -s http://localhost:5000/status"
echo -e "  ${CYAN}List targets:${NC}     curl -s http://localhost:5000/targets"
echo -e "  ${CYAN}Toggle target:${NC}    curl -X POST http://localhost:5000/targets/{id}/toggle"
echo -e "  ${CYAN}Run migration:${NC}    docker exec \$(curl -s http://localhost:5000/api/containers/config-manager | grep -o '\"container_name\":\"[^\"]*\"' | cut -d'\"' -f4) python3 /app/scripts/migrate_yaml_to_db.py"

echo
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${WHITE}💡 Tips & Troubleshooting${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  • ${YELLOW}Change Grafana admin password on first login${NC}"
echo -e "  • ${YELLOW}Keep the InfluxDB API token secure${NC}"
echo -e "  • ${YELLOW}--show-secrets prints values; it refuses a pipe or a file unless${NC}"
echo -e "    ${YELLOW}you add --force, so they do not end up in a log by accident${NC}"
echo -e "  • ${YELLOW}Access web admin from any device on your network${NC}"
[ "$EDITION" = "basic" ] || echo -e "  • ${YELLOW}Check service health: curl http://localhost:${WEB_ADMIN_URL_PORT}/api/status${NC}"

# Check for common issues
echo
echo -e "${WHITE}🔍 Health Checks:${NC}"

# Check if ports are accessible
# bash's own /dev/tcp: `nc` is not installed on a stock Raspberry Pi OS,
# and every port read "not accessible" for as long as this used it.
check_port() {
    local port="$1" service="$2"
    # `timeout` is coreutils: absent on a stock macOS (Homebrew's wrapper
    # supplies it), so fall back to a plain connect there.
    local t=""; command -v timeout >/dev/null && t="timeout 2"
    if $t bash -c "exec 3<>/dev/tcp/127.0.0.1/$port" 2>/dev/null; then
        echo -e "  ${GREEN}✅ Port $port ($service) is accessible${NC}"
    else
        echo -e "  ${RED}❌ Port $port ($service) is not accessible${NC}"
    fi
}

# Only the ports this edition has.
check_port "$SMOKEPING_URL_PORT" "SmokePing"
if [ "$EDITION" != "basic" ]; then
    check_port "$WEB_ADMIN_URL_PORT" "Web Admin"
fi
if [ "$EDITION" = "pro" ]; then
    check_port 3000 "Grafana"
    if [ "$TSDB_TYPE" = "clickhouse" ]; then
        check_port 8123 "ClickHouse"
    else
        check_port 8086 "InfluxDB"
    fi
fi

# Check if the env file has been modified from defaults
if grep -q "supersecrettoken\|your-secret-key-here" "$ENV_FILE" 2>/dev/null; then
    echo
    echo -e "${RED}⚠️  WARNING: Default passwords detected!${NC}"
    echo -e "${YELLOW}   Run 'docker compose down && rm $ENV_FILE && docker compose up -d'${NC}"
    echo -e "${YELLOW}   to regenerate secure passwords.${NC}"
fi

echo
echo -e "${CYAN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║${WHITE}        📘 Documentation & Support           ${CYAN}║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════╝${NC}"
echo -e "  GitHub: ${BLUE}https://github.com/estcarisimo/smoking-pi${NC}"
echo -e "  Issues: ${BLUE}https://github.com/estcarisimo/smoking-pi/issues${NC}"
echo