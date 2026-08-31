#!/bin/bash
#
# Install the SmokePing monitoring skill into OpenClaw.
#
# Registering the MCP server is not enough: an agent that also has a shell
# will answer "how is my internet?" with `ping`, sound confident, and describe
# one instant instead of your recorded history. The skill is what redirects
# it — and it is a plain file copy that is easy to do once and then forget on
# every later change, which is what this script exists to prevent.
#
# Safe to re-run. It never edits your OpenClaw config, never restarts the
# gateway without being asked, and prints the two manual steps it cannot do.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && cd .. && pwd)"
SOURCE="${REPO_ROOT}/examples/openclaw/smokeping-monitoring/SKILL.md"
SKILLS_DIR="${OPENCLAW_SKILLS_DIR:-${HOME}/.openclaw/skills}"
DEST_DIR="${SKILLS_DIR}/smokeping-monitoring"
DEST="${DEST_DIR}/SKILL.md"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

usage() {
    cat <<EOF
Usage: $(basename "$0") [--check] [--reload]

  --check    Report whether the installed skill matches the repo, then exit
             non-zero if it is missing or stale. Changes nothing.
  --reload   After installing, reload the OpenClaw gateway so the new skill
             is picked up. Requires systemd --user.

With no flags: install (or update) the skill and print what to do next.
EOF
}

CHECK_ONLY=0
RELOAD=0
while [ $# -gt 0 ]; do
    case "$1" in
        --check)  CHECK_ONLY=1 ;;
        --reload) RELOAD=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo -e "${RED}Unknown option: $1${NC}"; usage; exit 2 ;;
    esac
    shift
done

if [ ! -f "$SOURCE" ]; then
    echo -e "${RED}✗ Skill not found at ${SOURCE}${NC}"
    echo "  Run this from a checkout of the smoking-pi repository."
    exit 1
fi

# --check: a stale skill is the failure this whole script exists to catch, and
# it is invisible — the agent keeps answering, just in the old shape.
if [ "$CHECK_ONLY" = "1" ]; then
    if [ ! -f "$DEST" ]; then
        echo -e "${YELLOW}✗ Not installed${NC} — no skill at ${DEST}"
        exit 1
    fi
    if cmp -s "$SOURCE" "$DEST"; then
        echo -e "${GREEN}✓ Up to date${NC} — ${DEST} matches the repo"
        exit 0
    fi
    echo -e "${YELLOW}✗ Stale${NC} — ${DEST} differs from the repo copy"
    echo "  The agent is answering from the OLD skill. Re-run without --check."
    exit 1
fi

mkdir -p "$DEST_DIR"

if [ -f "$DEST" ] && cmp -s "$SOURCE" "$DEST"; then
    echo -e "${GREEN}✓ Already up to date${NC} — ${DEST}"
    ALREADY=1
else
    # Keep one backup of a hand-tuned skill: the four deployment-specific
    # values in it (loss floor, timezone, host names, target names) are easy
    # to have edited in place and painful to reconstruct.
    if [ -f "$DEST" ]; then
        cp "$DEST" "${DEST}.bak"
        echo -e "${YELLOW}ℹ Previous skill backed up to ${DEST}.bak${NC}"
    fi
    cp "$SOURCE" "$DEST"
    echo -e "${GREEN}✓ Installed${NC} — ${DEST}"
    ALREADY=0
fi

echo
echo -e "${BOLD}Tune it for your deployment${NC}"
echo "  The skill ships with one real deployment's values and names four"
echo "  things to change at the top of the file: the host's names, the CPE"
echo "  loss floor, the timezone, and the example target names. A wrong loss"
echo "  floor makes the agent confidently wrong rather than obviously broken."
echo -e "  ${CYAN}\$EDITOR ${DEST}${NC}"

echo
echo -e "${BOLD}Then reload — the gateway caches skills and tools per session${NC}"
if [ "$RELOAD" = "1" ]; then
    if command -v openclaw >/dev/null 2>&1; then
        echo -e "  ${CYAN}openclaw mcp reload${NC}"
        openclaw mcp reload || echo -e "  ${YELLOW}(reload reported an error)${NC}"
    fi
    if systemctl --user list-unit-files 2>/dev/null | grep -q openclaw-gateway; then
        echo -e "  ${CYAN}systemctl --user restart openclaw-gateway${NC}"
        # `|| true` because of `set -e`: a failed restart would otherwise exit
        # here, skipping the "start a NEW chat session" instruction below --
        # the one step that cannot be skipped, and the reason a correct
        # install still looks like it did nothing.
        if systemctl --user restart openclaw-gateway; then
            echo -e "${GREEN}✓ Gateway restarted${NC}"
        else
            echo -e "${RED}✗ Gateway restart failed${NC} — the skill is"
            echo -e "  installed, but the gateway is still serving the old"
            echo -e "  one. Restart it however you run it, then continue."
        fi
    else
        echo -e "  ${YELLOW}No openclaw-gateway user unit found — restart it however you run it.${NC}"
    fi
else
    echo -e "  ${CYAN}openclaw mcp reload${NC}"
    echo -e "  ${CYAN}systemctl --user restart openclaw-gateway${NC}   # or however you run it"
    echo "  (or re-run this script with --reload)"
fi

echo
echo -e "${BOLD}${YELLOW}Start a NEW chat session.${NC}"
echo "  Existing threads keep the skill and tool set they started with, so an"
echo "  open conversation will keep answering in the old shape and look like"
echo "  the install failed."

if [ "$ALREADY" = "0" ]; then
    echo
    echo -e "Verify with: ${CYAN}openclaw skills list | grep smokeping${NC}"
fi
