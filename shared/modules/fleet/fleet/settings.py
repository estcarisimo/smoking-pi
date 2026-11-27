"""Fleet settings from the environment (editions/pro/.env.template, ## Fleet).

Every problem is a literal reason string: a setting's value never reaches a
log through here, only its name.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field

from .protocol import valid_https_url, valid_name

#: Lead modes v1 implements. The protocol carries the mode as an open token,
#: so `elected` (a later PR) needs no protocol change, only this list.
LEAD_MODES = ("dedicated", "node")
DEFAULT_LEAD_MODE = "node"
DEFAULT_SITE_NAME = "default"

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", re.ASCII)


def valid_node_id(value: str) -> bool:
    """A random (version 4) UUID, lowercase: what `smoking-pi` generates."""
    return bool(_UUID.fullmatch(value))


def default_node_name(node_id: str) -> str:
    """The name a node has until FLEET_NODE_NAME says otherwise. The command
    applies the same rule (cli/lib/fleet.sh)."""
    return f"node-{node_id[:8]}" if node_id else "node"


@dataclass(frozen=True)
class Settings:
    node_id: str
    node_name: str
    site_name: str
    lead_urls: tuple[str, ...]
    lead_mode: str
    problems: tuple[str, ...] = field(default=())

    @property
    def has_identity(self) -> bool:
        return valid_node_id(self.node_id)


def load(env: dict[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    problems: list[str] = []

    node_id = (env.get("FLEET_NODE_ID") or "").strip().lower()
    if not node_id:
        problems.append("FLEET_NODE_ID is empty: run 'smoking-pi fleet status' on the host once")
    elif not valid_node_id(node_id):
        problems.append("FLEET_NODE_ID is not a version 4 UUID")
        node_id = ""

    # Names are taken in NFC (protocol.valid_name): an editor that wrote "é"
    # decomposed is not a different, invalid name.
    node_name = (unicodedata.normalize("NFC", env.get("FLEET_NODE_NAME") or "").strip()
                 or default_node_name(node_id))
    if not valid_name(node_name):
        problems.append("FLEET_NODE_NAME: 1 to 64 letters, digits (any script), spaces "
                        "and . _ - ' ( )")
        node_name = default_node_name(node_id)

    site_name = (unicodedata.normalize("NFC", env.get("FLEET_SITE_NAME") or "").strip()
                 or DEFAULT_SITE_NAME)
    if not valid_name(site_name):
        problems.append("FLEET_SITE_NAME: 1 to 64 letters, digits (any script), spaces "
                        "and . _ - ' ( )")
        site_name = DEFAULT_SITE_NAME

    urls: list[str] = []
    bad_url = False
    for item in (env.get("FLEET_LEAD_URLS") or "").split(","):
        item = item.strip()
        if not item:
            continue
        if valid_https_url(item):
            if item not in urls:
                urls.append(item)
        else:
            bad_url = True
    if bad_url:
        problems.append("FLEET_LEAD_URLS: every item must be an https:// URL without "
                        "credentials, query or fragment")
    if len(urls) > 16:
        problems.append("FLEET_LEAD_URLS: at most 16")
        urls = urls[:16]

    mode = (env.get("FLEET_LEAD_MODE") or "").strip() or DEFAULT_LEAD_MODE
    if mode not in LEAD_MODES:
        problems.append("FLEET_LEAD_MODE: one of " + ", ".join(LEAD_MODES))
        mode = DEFAULT_LEAD_MODE

    return Settings(node_id, node_name, site_name, tuple(urls), mode, tuple(problems))
