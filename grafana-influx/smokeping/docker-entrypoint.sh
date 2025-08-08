#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
#  SmokePing container entry-point
#  • Waits (optionally) for InfluxDB to report healthy
#  • Starts fcgiwrap → Lighttpd (background)
#  • Launches the RRD → Influx exporter (background)
#  • Finally execs SmokePing in the foreground (PID 1)
# ──────────────────────────────────────────────────────────────
set -euo pipefail

_log() { printf '[entrypoint] %s\n' "$*"; }

# -------------------------------------------------------------------
# 0. Optional: wait up to 30 s for InfluxDB /health to go green
#     (Skip if INFLUX_URL or INFLUX_TOKEN is unset.)
# -------------------------------------------------------------------
if [[ -n "${INFLUX_URL:-}" && -n "${INFLUX_TOKEN:-}" ]]; then
  _log "Waiting for InfluxDB (${INFLUX_URL}) to become healthy…"
  for _ in {1..30}; do
    if curl -sf "${INFLUX_URL}/health" >/dev/null; then
      _log "InfluxDB is up ✓"
      break
    fi
    sleep 1
  done
fi

# -------------------------------------------------------------------
# 1. Start FastCGI wrapper (fcgiwrap) for Lighttpd CGI execution
# -------------------------------------------------------------------
_log 'Starting fcgiwrap…'
spawn-fcgi -s /var/run/fcgiwrap.socket -M 766 \
           -u www-data -g www-data /usr/sbin/fcgiwrap
chmod 666 /var/run/fcgiwrap.socket

# -------------------------------------------------------------------
# 2. Start Lighttpd (background)
# -------------------------------------------------------------------
_log 'Starting Lighttpd…'
lighttpd -D -f /etc/lighttpd/lighttpd.conf &
LIGHTTPD_PID=$!

# -------------------------------------------------------------------
# 3. Launch RRD → InfluxDB exporter (background, if creds present)
# -------------------------------------------------------------------
if [[ -n "${INFLUX_URL:-}" && -n "${INFLUX_TOKEN:-}" ]]; then
  _log "Launching RRD exporter → ${INFLUX_URL}"
  /usr/local/bin/rrd2influx.py &
  EXPORTER_PID=$!
else
  _log '⚠️  INFLUX_URL or INFLUX_TOKEN not set — exporter disabled'
fi

# -------------------------------------------------------------------
# 4. Auto-detect IPv6 and configure probes (zero-touch deployment)
# -------------------------------------------------------------------
_log 'Configuring IPv6 support (zero-touch detection)…'

# Check if IPv6 is available
check_ipv6() {
    # Check if IPv6 interfaces exist and test connectivity
    if [ -f /proc/net/if_inet6 ] && grep -v "^00000000000000000000000000000001" /proc/net/if_inet6 | grep -q "00 00"; then
        # Test global IPv6 connectivity
        if timeout 3 ping6 -c 1 2001:4860:4860::8888 >/dev/null 2>&1 || \
           timeout 3 ping6 -c 1 2606:4700:4700::1111 >/dev/null 2>&1; then
            return 0
        fi
    fi
    return 1
}

# Configure FPing6 probe dynamically
if check_ipv6; then
    _log 'IPv6 detected and reachable - enabling FPing6 probe'
    
    # Add FPing6 probe to configuration if not present
    if ! grep -q "FPing6" /etc/smokeping/config.d/Probes; then
        cat >> /etc/smokeping/config.d/Probes <<EOF

+ FPing6
binary = /usr/bin/fping6
step = 300
pings = 10
EOF
        _log 'FPing6 probe added to configuration'
    fi
else
    _log 'IPv6 not available - IPv4 only mode'
    
    # Remove any FPing6 probe references from configuration
    sed -i '/+ FPing6/,/^$/d' /etc/smokeping/config.d/Probes 2>/dev/null || true
    # Also disable any IPv6 targets by commenting them out
    sed -i '/probe = FPing6/,/^$/{s/^/# /}' /etc/smokeping/config.d/Targets 2>/dev/null || true
    _log 'IPv6 probes and targets disabled'
fi

# -------------------------------------------------------------------
# 5. Run SmokePing in the foreground (keeps container alive)
# -------------------------------------------------------------------
_log 'Starting SmokePing…'
exec smokeping --nodaemon 

# # ────────────────────────────────────────────────────────────
# #  Alternative entry-point script (commented out)
# #  • This version is simpler, but does not wait for InfluxDB.
# #  • It starts fcgiwrap and Lighttpd, then runs SmokePing.
# #  • The exporter is not launched in this version.
# #!/usr/bin/env bash
# # ────────────────────────────────────────────────────────────
# #  SmokePing container entry-point
# #  • Starts fcgiwrap (FastCGI), Lighttpd, the RRD→Influx exporter,
# #    then runs SmokePing in the foreground (PID 1).
# #  • Influx credentials are expected via env vars injected by
# #    Docker Compose (.env) — nothing hard-coded here.
# # ────────────────────────────────────────────────────────────
# set -euo pipefail

# _log() { printf '[entrypoint] %s\n' "$*"; }

# # -----------------------------------------------------------------
# # 0. Optionally wait for InfluxDB to come up (30 s max, silent fail)
# # -----------------------------------------------------------------
# if [[ -n "${INFLUX_URL:-}" && -n "${INFLUX_TOKEN:-}" ]]; then
#   for _ in {1..30}; do
#     curl -sf "${INFLUX_URL}/health" && { _log "InfluxDB is up"; break; } || true
#     sleep 1
#   done
# fi

# # -----------------------------------------------------------------
# # 1. Start FastCGI wrapper (fcgiwrap) — needed by Lighttpd CGI
# # -----------------------------------------------------------------
# _log 'Starting fcgiwrap…'
# spawn-fcgi -s /var/run/fcgiwrap.socket -M 766 \
#            -u www-data -g www-data /usr/sbin/fcgiwrap
# chmod 666 /var/run/fcgiwrap.socket

# # -----------------------------------------------------------------
# # 2. Start Lighttpd in the background
# # -----------------------------------------------------------------
# _log 'Starting Lighttpd…'
# lighttpd -D -f /etc/lighttpd/lighttpd.conf &
# LIGHTTPD_PID=$!

# # -----------------------------------------------------------------
# # 3. Run RRD → InfluxDB exporter (if creds present)
# # -----------------------------------------------------------------
# if [[ -n "${INFLUX_URL:-}" && -n "${INFLUX_TOKEN:-}" ]]; then
#   _log "Launching RRD exporter → ${INFLUX_URL}"
#   /usr/local/bin/rrd2influx.py &
#   EXPORTER_PID=$!
# else
#   _log "⚠️  INFLUX_URL or INFLUX_TOKEN not set — exporter DISABLED."
# fi

# # -----------------------------------------------------------------
# # 4. Run SmokePing in the foreground (PID 1)
# # -----------------------------------------------------------------
# _log 'Starting SmokePing…'
# exec /usr/lib/smokeping/bin/smokeping --nodaemon


#!/usr/bin/env bash
# set -euo pipefail

# # 1) Start the helpers that must run in the background
# spawn-fcgi -s /var/run/fcgiwrap.socket -U www-data -G www-data /usr/sbin/fcgiwrap
# service lighttpd start

# # 2) Hand PID 1 to SmokePing so it receives Docker stop signals
# exec smokeping --nodaemon --config=/etc/smokeping/config