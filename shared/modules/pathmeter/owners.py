"""Origin AS of the routers on the paths.

From the process's routing table (``common.asn.use_table``: originet over
CAIDA's RouteViews prefix-to-AS snapshot, with the exchange members of
CAIDA's IXP dataset), looked up locally, every address at once and by its
announced prefix. Nothing is asked of anyone else: until the table is
loaded, or for an address it has no route for, the hop has no AS and the AS
path skips it.
"""

from __future__ import annotations

from common import asn as lookup


class Owners:
    def __init__(self, table=lambda: lookup._TABLE) -> None:
        self._table = table

    def of(self, addr: str | None) -> int | None:
        if not addr:
            return None
        table = self._table() if callable(self._table) else self._table
        if table is None or not table.ready:
            return None
        try:
            return table.lookup(addr) or None
        except ValueError:
            return None
