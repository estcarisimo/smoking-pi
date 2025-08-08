#!/usr/bin/env bash
set -euo pipefail

# 1) Start the helpers that must run in the background
spawn-fcgi -s /var/run/fcgiwrap.socket -U www-data -G www-data /usr/sbin/fcgiwrap
service lighttpd start

# 2) Hand PID 1 to SmokePing so it receives Docker stop signals
exec smokeping --nodaemon --config=/etc/smokeping/config