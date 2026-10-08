# shellcheck shell=bash
# smoking-pi budget: `budget` and `traffic`, what measuring costs.
# Sourced by cli/smoking-pi, never run on its own.

cmd_budget() {
    # What the configured measurements cost against the ceilings
    # (config-manager/budget.py), asked of the running API from inside its
    # container: the API token stays in the container's environment.
    local a
    for a in "$@"; do
        case "$a" in --json) ;; -h|--help) usage; return 0 ;;
            *) echo "unknown option $a (--json)" >&2; return 2 ;; esac
    done
    need_edition
    if ! grep -q '^  config-manager:' "$EDITION_DIR/docker-compose.yml"; then
        echo "The measurement budget reads config-manager's generated config; the $SMOKING_PI_EDITION edition does not ship it." >&2
        return 1
    fi
    if [ -z "$(compose ps -q --status running config-manager 2>/dev/null)" ]; then
        echo "config-manager is not running: $(cli_name) up" >&2
        return 1
    fi
    compose exec -T config-manager python budget.py "$@"
}

cmd_traffic() {
    # The traffic ledger (config-manager/traffic.py over the meters' state
    # files), asked of the running API from inside its container, like
    # budget: the API token stays in the container's environment.
    local a
    for a in "$@"; do
        case "$a" in --json) ;; -h|--help) usage; return 0 ;;
            *) echo "unknown option $a (--json)" >&2; return 2 ;; esac
    done
    need_edition
    # The meters (the netmeter, SmokePing's uplink exporter) are Pro's.
    if ! grep -q '^  netmeter:' "$EDITION_DIR/docker-compose.yml"; then
        echo "Traffic accounting is a Pro feature; the $SMOKING_PI_EDITION edition does not ship it." >&2
        return 1
    fi
    if [ -z "$(compose ps -q --status running config-manager 2>/dev/null)" ]; then
        echo "config-manager is not running: $(cli_name) up" >&2
        return 1
    fi
    compose exec -T config-manager python traffic.py "$@"
}
