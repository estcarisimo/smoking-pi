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
# 4. Run SmokePing in the foreground (keeps container alive)
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