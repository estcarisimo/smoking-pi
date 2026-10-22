# shellcheck shell=bash
# smoking-pi taxonomy: what kind of service each target is (docs/taxonomy.md).
# Sourced by cli/smoking-pi, never run on its own.

cmd_taxonomy() {
    # The service labels (config-manager/taxonomy.py), asked of the running
    # API from inside its container like budget: the API token stays in
    # the container's environment.
    local sub="${1:-list}" a n=0
    [ $# -eq 0 ] || shift
    case "$sub" in
        list|unknown)
            for a in "$@"; do
                case "$a" in --json) ;; -h|--help) usage; return 0 ;;
                    *) echo "unknown option $a (--json)" >&2; return 2 ;; esac
            done ;;
        set)
            # set TARGET SERVICE CAT[,CAT...] [--note TEXT]
            local note_next=0
            for a in "$@"; do
                if [ "$note_next" = 1 ]; then note_next=0; continue; fi
                case "$a" in
                    --note) note_next=1 ;;
                    -h|--help) usage; return 0 ;;
                    -*) echo "unknown option $a (--note)" >&2; return 2 ;;
                    *) n=$((n + 1)) ;;
                esac
            done
            if [ "$note_next" = 1 ]; then echo "--note needs a text" >&2; return 2; fi
            if [ "$n" -ne 3 ]; then
                echo "usage: $(cli_name) taxonomy set TARGET SERVICE CATEGORY[,CATEGORY...] [--note TEXT]" >&2
                return 2
            fi ;;
        reset)
            for a in "$@"; do
                case "$a" in -h|--help) usage; return 0 ;;
                    -*) echo "unknown option $a" >&2; return 2 ;; esac
            done
            if [ $# -ne 1 ]; then
                echo "usage: $(cli_name) taxonomy reset TARGET" >&2
                return 2
            fi ;;
        -h|--help) usage; return 0 ;;
        *) echo "unknown taxonomy command $sub (list, unknown, set, reset)" >&2; return 2 ;;
    esac
    need_edition
    if ! grep -q '^  config-manager:' "$EDITION_DIR/docker-compose.yml"; then
        echo "Service labels live in config-manager; the $SMOKING_PI_EDITION edition does not ship it." >&2
        return 1
    fi
    if [ -z "$(compose ps -q --status running config-manager 2>/dev/null)" ]; then
        echo "config-manager is not running: $(cli_name) up" >&2
        return 1
    fi
    compose exec -T config-manager python taxonomy.py "$sub" "$@"
}
