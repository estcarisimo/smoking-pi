"""Who runs the infrastructure behind each target: operator and kind.

The shared detector (shared.py) asks whether loss reached *many kinds of
infrastructure at once*, not many target names: three names on one CDN are
one piece of infrastructure, not three. So each target is tagged by what it
reaches, not by a list of targets: its network from public data, and that
network's kind from a short curated table of well-known networks:

1. its host, from the ``target_host`` measurement the exporter writes;
2. the host's address and CNAME chain, from DNS (the chain is what tells
   CloudFront from the rest of Amazon);
3. the address's origin AS and its name, from the routing table in the
   state volume (originet over CAIDA's RouteViews snapshot, CAIDA's AS2Org
   names), looked up locally;
4. the operator and its kind from :data:`KNOWN`, a table of well-known
   networks, then the AS roles derived from Linnaeus
   (``common/data/asn_roles.csv``). An AS in neither is its own operator,
   of kind ``other``.

The lookups themselves (steps 2 to 4) are ``common/asn.py``, shared with
the pathmeter's hops, and the per-target cache is ``common/operators.py``,
shared with the exporter's ``target_labels``; this module keeps that cache
in the inference state volume.

Kinds: a multi-tenant CDN (many sites on one edge), a content provider's own
network, a cache embedded near the access network, a cloud or origin server,
an academic network, an access ISP, a transit carrier, DNS infrastructure.
A target on the local network counts as no operator here, as before.
PeeringDB's network type was tried first and is too coarse: it calls nearly
all of these "Content".

Lookups are a few DNS queries per target for its address, then the local
routing table for its owner, cached in the state volume (addresses for a
day, owners for a week). A failed lookup is cached
for an hour, so a host that never resolves cannot hold the budget forever;
until a lookup succeeds the target is ``unknown`` and counts as no operator.
"""

from __future__ import annotations

import os
from pathlib import Path

from common.asn import (  # noqa: F401 -- re-exported: tags.CDN, tags.classify...
    ACADEMIC, ACCESS, AMAZON, CACHE, CDN, CLOUD, CLOUDFRONT_HINTS, CONTENT, DNS_INFRA,
    KNOWN, LOCAL, OTHER, TRANSIT, UNKNOWN, _resolver, classify, origin, resolve,
)
from common.operators import (  # noqa: F401 -- re-exported: tags.ADDRESS_TTL...
    ADDRESS_TTL, BUDGET, FAILED_TTL, OWNER_TTL, Operators,
)


def _state_path() -> Path:
    return Path(os.environ.get("INFERENCE_STATE_DIR", "/var/lib/inference")) / "tags.json"


class Tags(Operators):
    """Operator and kind per target, cached in ``tags.json``."""

    def __init__(self, path: Path | None = None, clock=None,
                 resolve_fn=resolve, origin_fn=origin) -> None:
        kwargs = {"clock": clock} if clock is not None else {}
        super().__init__(path or _state_path(), resolve_fn=resolve_fn,
                         origin_fn=origin_fn, **kwargs)

    def of(self, target: str, hosts: dict[str, str]) -> tuple[str, str]:
        """(operator, kind) of a target; ``unknown`` until its lookups succeed.
        A target on the local network is no kind of infrastructure here."""
        operator, kind = super().of(target, hosts)
        return (UNKNOWN, UNKNOWN) if kind == LOCAL else (operator, kind)
