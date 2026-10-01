#!/usr/bin/with-contenv bash

# Archive, before SmokePing starts, every RRD it would refuse to load.
#
# SmokePing dies when a target's RRD has a different step or ping count
# than its probe ("RRD parameter mismatch"), and every target stops with
# it. config-manager runs rrd_guard.py before each reload it sends, but it
# cannot reach a SmokePing that is not running: on an upgrade that
# recreates both containers and changes a probe's cadence, SmokePing would
# start on the new Probes file and die on every restart. This runs the same
# guard at start, against cadence.json, which config-manager writes next to
# Targets and Probes. Mismatches are moved to /data/.archive/, never
# deleted. It never fails the container's start.

set -u

MAP="/config/generated/cadence.json"
GUARD="/exporters/rrd_guard.py"

if [ ! -f "$MAP" ] || [ ! -f "$GUARD" ]; then
    echo "[rrd-guard] nothing to check (no ${MAP} yet, or no guard in this image)"
    exit 0
fi

echo "[rrd-guard] $(python3 "$GUARD" --file "$MAP" 2>&1)"
exit 0
