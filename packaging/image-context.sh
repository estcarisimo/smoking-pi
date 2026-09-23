#!/bin/sh
# image-context.sh SERVICE -- print "CONTEXT DOCKERFILE" for a service image.
#
# The one place that says where each image builds from, read by ci.yml (a
# build on every PR) and release.yml (the published build), so a PR proves
# exactly the build the tag will run. The compose files declare the same
# contexts; packaging/check-images.py fails when the service list, the
# Dockerfiles and the compose files disagree.
#
# Images that copy shared/modules/common or the exporters build from
# `shared/`; the rest from their own module directory.
set -eu
service="${1:?usage: image-context.sh SERVICE}"
dockerfile="shared/modules/$service/Dockerfile"
[ -f "$dockerfile" ] || { echo "no Dockerfile for '$service' at $dockerfile" >&2; exit 1; }
case "$service" in
    ai-insights|alerter|mcp-server|web-admin|smokeping) context=shared ;;
    *) context="shared/modules/$service" ;;
esac
echo "$context $dockerfile"
