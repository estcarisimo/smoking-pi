#!/bin/sh
# image-context.sh SERVICE -- print "CONTEXT DOCKERFILE" for a service image.
#
# The one place that says where each image builds from, read by ci.yml (a
# build on every PR), release.yml (the published build) and mcp-image.yml
# (the mcp-server image, on a GitHub release or a manual run), so a PR
# proves exactly the build those workflows will run. The compose files
# declare the same contexts; packaging/check-images.py fails when the
# service list, the Dockerfiles and the compose files disagree.
#
# Images that copy shared/modules/common or the exporters build from
# `shared/`; the rest from their own module directory.
set -eu
service="${1:?usage: image-context.sh SERVICE}"
dockerfile="shared/modules/$service/Dockerfile"
[ -f "$dockerfile" ] || { echo "no Dockerfile for '$service' at $dockerfile" >&2; exit 1; }
case "$service" in
    ai-insights|alerter|inference|mcp-server|pathmeter|web-admin|smokeping) context=shared ;;
    *) context="shared/modules/$service" ;;
esac
echo "$context $dockerfile"
