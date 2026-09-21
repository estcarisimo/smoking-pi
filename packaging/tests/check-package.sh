#!/usr/bin/env bash
# Installs a built .deb on THIS host with apt -- so the host's own package
# resolver picks the Docker engine, CLI and Compose from the repositories
# it has -- and checks what an installed package must satisfy here. The
# release workflow runs it on every supported host (docs/packaging.md,
# "Supported hosts"): Ubuntu VMs, where it also starts an edition with the
# release's images, and Debian containers, where there is no daemon and it
# stops at what a package can prove without one.
#
#   sudo packaging/tests/check-package.sh dist/smoking-pi_<ver>_all.deb [options]
#     --version V        the version the package must report (a 0.0.0~ throwaway
#                        is not checked)
#     --expect-engine P  the engine package apt must have chosen (docker-ce
#                        wherever Docker's repository is configured, else
#                        docker.io)
#     --start EDITION    needs a daemon: `smoking-pi install` that edition,
#                        wait for its web UI, enable and start the unit, stop
#                        it, and only then remove the package
#     --image-tag TAG    for a throwaway build whose images are tagged
#                        differently from the package version (test-* tags)
#
# Runs as root and leaves the host without the package. Local use: any
# distro container on the Pi (mount the .deb), or a scratch VM.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

DEB="" VERSION="" ENGINE="" START="" IMAGE_TAG=""
while [ $# -gt 0 ]; do
    case "$1" in
        --version) VERSION="$2"; shift 2 ;;
        --expect-engine) ENGINE="$2"; shift 2 ;;
        --start) START="$2"; shift 2 ;;
        --image-tag) IMAGE_TAG="$2"; shift 2 ;;
        -*) echo "unknown option $1" >&2; exit 2 ;;
        *) DEB="$1"; shift ;;
    esac
done
[ -n "$DEB" ] && [ -f "$DEB" ] || { echo "usage: $0 <package.deb> [--version V] [--expect-engine docker-ce|docker.io] [--start EDITION] [--image-tag TAG]" >&2; exit 2; }
[ "$(id -u)" = 0 ] || { echo "run as root: apt installs and the state directories are root's" >&2; exit 2; }
DEB="$(readlink -f "$DEB")"

fail() { echo "FAIL: $*" >&2; exit 1; }
# Not sourced: os-release sets VERSION, which is this script's option.
PRETTY_NAME=$(sed -n 's/^PRETTY_NAME="\(.*\)"/\1/p' /etc/os-release)
echo "== $PRETTY_NAME, $(uname -m), $(dpkg --print-architecture)"

# 1. The host's resolver, worst case: no Recommends. A missing alternative
# (Debian 12 without Docker's repository, Debian 13's split CLI) fails here.
apt-get update -qq
apt-get install -y -qq --no-install-recommends "$DEB" >/tmp/check-package.apt.log 2>&1 \
    || { tail -20 /tmp/check-package.apt.log; fail "apt could not install the package from this host's repositories"; }
picked=$(dpkg-query -W -f '${db:Status-Status} ${Package} ${Version}\n' docker-ce docker.io docker-ce-cli docker-cli \
    docker-compose-plugin docker-compose-v2 docker-compose 2>/dev/null | awk '$1 == "installed" { print "   " $2 " " $3 }')
echo "-- apt chose:"; echo "$picked"
if [ -n "$ENGINE" ]; then
    echo "$picked" | grep -q "^   $ENGINE " || fail "expected the $ENGINE engine, apt chose otherwise"
fi
# The CLI and the Compose plugin are two packages on some hosts: both must be there.
docker --version || fail "no docker CLI after install"
docker compose version || fail "no 'docker compose' plugin after install"
python3 --version
python3 -c 'import yaml' || fail "python3-yaml not importable by $(command -v python3)"

# 2. What the package reports and where it put things.
v=$(smoking-pi version); echo "smoking-pi version: $v"
if [ -n "$VERSION" ]; then
    case "$VERSION" in 0.0.0~*) ;; *) [ "$v" = "$VERSION" ] || fail "package reports $v, expected $VERSION" ;; esac
fi
deb_version=$(sed -n 's/^SMOKING_PI_VERSION=//p' /etc/default/smoking-pi)
[ -n "$deb_version" ] || fail "/etc/default/smoking-pi has no SMOKING_PI_VERSION"
out=$(smoking-pi paths); echo "$out"
# Installed at /usr/bin, the command must find the tree in /opt -- the
# first packaged run resolved "one directory up" to / instead.
echo "$out" | grep -q 'home:     /opt/smoking-pi' || fail "home is not /opt/smoking-pi"
echo "$out" | grep -q 'mode:     packaged' || fail "not in packaged mode"
echo "$out" | grep -q 'env:      /etc/smoking-pi/env' || fail "env file not relocated"
echo "$out" | grep -q "/<service>:$deb_version" || fail "images not pinned to the package version"
[ -d /etc/smoking-pi/config ] || fail "config dir missing"
[ -d /var/lib/smoking-pi/output ] || fail "output dir missing"
[ "$(stat -c %a /etc/smoking-pi)" = 750 ] || fail "/etc/smoking-pi is not 0750"
systemd-analyze verify /lib/systemd/system/smoking-pi.service || fail "the unit does not verify"

# 3. The doctor's static checks run from /opt: the package carries
# everything they read. A skipped doctor is a failed check.
smoking-pi doctor | tee /tmp/check-package.doctor.out
grep -q ' ok, 0 warn, 0 fail' /tmp/check-package.doctor.out || fail "doctor did not pass"

# 4. Every edition renders with THIS host's Compose, in the packaged layout
# (the overlay's `!override` needs Compose >= 2.24), from a filled-in
# template -- no daemon needed. Only what a package changes is asserted:
# the state mounts must have left /opt.
mkdir -p /etc/smoking-pi
for ed in basic standard pro; do
    sed 's/^\([A-Z_]*\)=$/\1=dummy/' "/opt/smoking-pi/editions/$ed/.env.template" > /tmp/check-package.env
    files=(-f docker-compose.yml)
    [ -f "/opt/smoking-pi/editions/$ed/docker-compose.packaged.yml" ] && files+=(-f docker-compose.packaged.yml)
    # shellcheck disable=SC1091
    ( cd "/opt/smoking-pi/editions/$ed" && set -a && . /etc/default/smoking-pi && set +a \
        && docker compose --env-file /tmp/check-package.env "${files[@]}" config --format json ) > /tmp/check-package.cfg.json \
        || fail "compose config failed for $ed with $(docker compose version --short)"
    python3 - "$ed" <<'PY' || exit 1
import json, sys
cfg = json.load(open("/tmp/check-package.cfg.json")); ed = sys.argv[1]
binds = [v["source"] for s in cfg["services"].values() for v in s.get("volumes", []) if v.get("type") == "bind"]
state = [b for b in binds if "/config-manager/config" in b or "/config-manager/output" in b or b.endswith("/.env")]
if state:
    sys.exit(f"FAIL: {ed} still mounts state from the tree: {state}")
print(f"compose config {ed}: OK, {len(cfg['services'])} services")
PY
done

# 5. `install` refuses to run over an existing env file -- the guard that
# stops a reinstall from rotating live secrets.
touch /etc/smoking-pi/env
! smoking-pi install --yes 2>/dev/null || fail "install ran over an existing env file"
rm -f /etc/smoking-pi/env

# 6. With a daemon: the real first install of an edition, the unit around
# it, a clean stop. The images are the release's own, pulled from GHCR.
if [ -n "$START" ]; then
    if [ -n "$IMAGE_TAG" ] && [ "$IMAGE_TAG" != "$deb_version" ]; then
        # A throwaway build: its images carry the git tag, not a Debian
        # version. The conffile is the documented place to say so.
        sed -i "s/^SMOKING_PI_VERSION=.*/SMOKING_PI_VERSION=$IMAGE_TAG/" /etc/default/smoking-pi
    fi
    docker info >/dev/null || fail "no Docker daemon to start $START with"
    smoking-pi install --edition "$START" --yes
    grep -qx "SMOKING_PI_EDITION=$START" /etc/default/smoking-pi || fail "install did not record the edition"
    port=$(sed -n 's/^SMOKEPING_PORT=//p' /etc/smoking-pi/env); port="${port:-80}"
    for _ in $(seq 60); do
        code=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$port/" || true)
        case "$code" in 2*|3*) break ;; esac
        sleep 5
    done
    case "$code" in 2*|3*) echo "web UI answers on :$port ($code)" ;; *) smoking-pi logs 2>&1 | tail -30; fail "web UI on :$port did not answer (last: $code)" ;; esac
    smoking-pi status
    if [ -d /run/systemd/system ]; then
        systemctl enable --now smoking-pi || fail "the unit did not start"
        systemctl is-active smoking-pi || fail "the unit is not active"
        systemctl stop smoking-pi || fail "the unit did not stop"
    else
        smoking-pi down
    fi
    project=$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' /etc/smoking-pi/env)
    [ -z "$(docker ps -q --filter "label=com.docker.compose.project=${project:-$START}")" ] || fail "containers still running after stop"
fi

# 7. Removal keeps the state: apt remove never deletes what a user
# configured or measured (backlog #6 is the purge policy).
touch /etc/smoking-pi/env
apt-get remove -y -qq smoking-pi >/dev/null
[ ! -e /usr/bin/smoking-pi ] || fail "CLI left behind"
[ -f /etc/smoking-pi/env ] || fail "removal deleted the env file"
[ -d /var/lib/smoking-pi/output ] || fail "removal deleted the output dir"
echo "== package OK on $PRETTY_NAME ($(dpkg --print-architecture)): $(echo "$picked" | tr -s ' \n' ' ')"
