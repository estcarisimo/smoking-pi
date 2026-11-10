"""What the detectors read: each ICMP target's pings and loss from InfluxDB.

``latency`` points carry one field per ping of a probe cycle (``ping1`` ..
``pingN``, seconds, SORTED by SmokePing: the order the pings were sent is
lost) and ``loss`` as a 0-1 ratio. A lost ping writes no ``pingK`` field, so
the pings of a cycle are the answered ones, exactly what Jitterbug expects
as input (one row per RTT sample, lost ones absent).
"""

from __future__ import annotations

import logging
import os
import re

import numpy as np
import pandas as pd

import bins
import deaf
from common import microcuts
from common.microcuts import uplink_flux
from common.tsdb import flux_str, influx_bucket, query_influx

# Influx tag vocabulary (not the database's: top_sites is topsites here).
DEFAULT_CATEGORIES = "topsites,custom,cpe"
PING_FIELD = re.compile(r"^ping\d+$")
DEAF_LOOKBACK_S = 3600
log = logging.getLogger("inference.series")


def categories() -> list[str]:
    raw = os.environ.get("INFERENCE_CATEGORIES", "") or DEFAULT_CATEGORIES
    return [c.strip() for c in raw.split(",") if c.strip()]


def explicit_targets() -> list[str]:
    raw = os.environ.get("INFERENCE_TARGETS", "")
    return [t.strip() for t in raw.split(",") if t.strip()]


def targets(start: int, query=query_influx) -> list[tuple[str, str]]:
    """(target, category) of every ICMP target that answered since ``start``:
    INFERENCE_TARGETS when set, else the targets in INFERENCE_CATEGORIES."""
    wanted = set(explicit_targets())
    cats = set(categories())
    flux = (
        f"from(bucket: {flux_str(influx_bucket())}) "
        f"|> range(start: {int(start)}) "
        '|> filter(fn: (r) => r._measurement == "latency" and r._field == "loss") '
        '|> keep(columns: ["target", "category"]) '
        '|> distinct(column: "target")'
    )
    found = set()
    for row in query(flux):
        target, category = row.get("target") or row.get("_value"), row.get("category") or ""
        if not target:
            continue
        if wanted and target not in wanted:
            continue
        if not wanted and category not in cats:
            continue
        found.add((target, category))
    return sorted(found)


def fetch(target: str, start: int, query=query_influx) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(pings, loss) of ``target`` since ``start`` (epoch seconds).

    pings: one row per answered ping, columns ``epoch`` (s) and ``values``
    (ms), the input format of Jitterbug. loss: one row per probe cycle,
    ``epoch`` and ``loss_pct`` (0-100)."""
    flux = (
        f"from(bucket: {flux_str(influx_bucket())}) "
        f"|> range(start: {int(start)}) "
        f'|> filter(fn: (r) => r._measurement == "latency" and r.target == {flux_str(target)}) '
        '|> filter(fn: (r) => r._field == "loss" or r._field =~ /^ping[0-9]+$/) '
        '|> keep(columns: ["_time", "_field", "_value"])'
    )
    pings, loss = [], []
    for row in query(flux):
        field, value, when = row.get("_field"), row.get("_value"), row.get("_time")
        if value is None or when is None:
            continue
        epoch = int(pd.Timestamp(when).timestamp())
        if field == "loss":
            loss.append((epoch, min(1.0, max(0.0, float(value))) * 100.0))
        elif PING_FIELD.match(str(field)):
            pings.append((epoch, float(value) * 1000.0))
    p = pd.DataFrame(pings, columns=["epoch", "values"]).sort_values("epoch", kind="stable")
    lo = pd.DataFrame(loss, columns=["epoch", "loss_pct"]).sort_values("epoch", kind="stable")
    return p.reset_index(drop=True), lo.reset_index(drop=True)


def deaf_rows(start: int, query=query_influx) -> list[dict]:
    """The host's deaf Wi-Fi samples since ``start``: the same query the
    alerter and the MCP server use to tell a radio hang from a cut
    (``common.microcuts.uplink_flux``). Empty on a wired host; raises when
    the database cannot be read (deaf_bins then labels nothing deaf)."""
    rows = list(query(uplink_flux(str(int(start)))))
    # The shared query stops at MAX_ROWS * 4 samples, oldest first: about
    # 55 h of deaf time at a 10 s cadence. Past that the newest hangs would
    # read as loss again, so say so rather than let it pass in silence.
    if len(rows) >= microcuts.MAX_ROWS * 4:
        log.warning("deaf samples truncated at %d rows: the newest deaf periods may "
                    "be labeled loss; a %d-row week means the radio was deaf most of it",
                    len(rows), len(rows))
    return rows


def deaf_bins(start: int, now: int, query=query_influx, directory=None) -> set[int]:
    """The deaf bins since ``start``, closed ones from the state volume
    (bins.py): the full query measured 4.5 s a pass on the staging Pi. Each
    read starts DEAF_LOOKBACK_S early, because uplink_flux's ``difference``
    and ``fill`` need the samples before a bin to judge its first ones. An
    unreadable database labels nothing deaf and caches nothing."""
    def fetch(since: int, stop: int) -> pd.DataFrame:
        table = deaf.counts(deaf_rows(since - DEAF_LOOKBACK_S, query))
        return table[(table["bin"] >= since) & (table["bin"] < stop)]

    try:
        return deaf.bins_of(bins.read("deaf", start, now, fetch, deaf.COUNT_COLUMNS,
                                      directory))
    except Exception:  # noqa: BLE001 -- a label is optional; the detectors are not
        log.warning("could not read the deaf samples", exc_info=True)
        return set()


# ───────────────────────── the triage's layers (triage.py) ─────────────────────────

# A target name inside a Flux regex: SmokePing names are letters, digits,
# "_" and "-"; any other name gets no TCP/HTTP probes rather than a regex
# built from it.
SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
TRIAGE_COLUMNS = ["bin", "layer", "probe", "med", "loss"]
# rrd2influx's MAX_BACKFILL is 24 h: an hour more than that is re-read.
TRIAGE_TAIL_S = 25 * 3600
TRIAGE_REFRESH_S = 7 * 86400
WIFI_FIELDS = ("signal_dbm", "tx_bitrate_mbps", "uplink")


def _epoch_of(value) -> int | None:
    if value is None:
        return None
    try:
        return int(pd.Timestamp(value).timestamp())
    except (TypeError, ValueError):
        return None


def _triage_flux(targets: list[str], since: int, stop: int) -> list[str]:
    """The raw points of every layer but ICMP and the radio, and the radio's
    bin means. Raw, not aggregated: InfluxDB cannot push a median down, and
    aggregateWindow(fn: median) over 13 days of one destination's HTTP
    measured 2.1 s on the staging Pi against 0.27 s for the raw points."""
    bucket = flux_str(influx_bucket())
    rng = f"|> range(start: {int(since)}, stop: {int(stop)}) "
    names = [t for t in targets if SAFE_NAME.fullmatch(t)]
    out = []
    if names:
        alt = "|".join(names)
        out.append(
            f"from(bucket: {bucket}) {rng}"
            '|> filter(fn: (r) => (r._measurement == "tcp_latency" or '
            'r._measurement == "http_latency") and '
            f"r.target =~ /^({alt})_(h[123]|tcp(443)?)$/) "
            '|> filter(fn: (r) => r._field == "median" or r._field == "loss") '
            '|> keep(columns: ["_time", "_measurement", "_field", "_value", "target"])')
    out.append(
        f"from(bucket: {bucket}) {rng}"
        '|> filter(fn: (r) => r._measurement == "dns_latency" and r.category == "dns") '
        '|> filter(fn: (r) => r._field == "median" or r._field == "loss") '
        '|> keep(columns: ["_time", "_measurement", "_field", "_value", "target"])')
    out.append(
        f"from(bucket: {bucket}) {rng}"
        '|> filter(fn: (r) => r._measurement == "dns_resolution" and r.path == "upstreams" '
        'and r._field == "p50") '
        '|> keep(columns: ["_time", "_measurement", "_field", "_value"])')
    # The gateway is probed every 30 s: ten times the points of a SmokePing
    # probe. Its bin medians are cheaper computed in InfluxDB (1.4 s for 13
    # days on the staging Pi) than 70 000 raw points parsed here (3.3 s); a
    # cycle that lost everything writes a median of 0, which is no latency.
    # Its loss is a mean, which InfluxDB pushes down.
    for field, fn, keep in (("median", "median", " and r._value > 0.0"),
                            ("loss", "mean", "")):
        out.append(
            f"from(bucket: {bucket}) {rng}"
            f'|> filter(fn: (r) => r._measurement == "cpe_latency" and r._field == "{field}"'
            f"{keep}) "
            f'|> aggregateWindow(every: 15m, fn: {fn}, createEmpty: false, timeSrc: "_start") '
            '|> keep(columns: ["_time", "_measurement", "_field", "_value", "target"])')
    out.append(
        f"from(bucket: {bucket}) {rng}"
        '|> filter(fn: (r) => r._measurement == "wifi_link" and '
        '(r._field == "signal_dbm" or r._field == "tx_bitrate_mbps" or r._field == "uplink")) '
        '|> group(columns: ["_measurement", "_field", "interface"]) '
        '|> aggregateWindow(every: 15m, fn: mean, createEmpty: false, timeSrc: "_start") '
        '|> keep(columns: ["_time", "_measurement", "_field", "_value", "interface"])')
    return out


def triage_table(rows: list[dict]) -> pd.DataFrame:
    """Rows of :func:`_triage_flux` as triage bins (triage.COLUMNS): per
    layer, probe and 15-minute bin, the median of the probe's medians in ms
    and its mean loss in percent. Units as stored: ``tcp_latency``,
    ``http_latency``, ``dns_latency`` and ``dns_resolution`` in seconds with
    loss 0-1; ``cpe_latency`` in ms with loss 0-100 (already one value per
    bin). The radio: the interface that carried the default route in each
    bin (``uplink`` mean of at least a half), its signal and tx rate."""
    empty = pd.DataFrame(columns=TRIAGE_COLUMNS).astype({"bin": "int64"})
    if not rows:
        return empty
    df = pd.DataFrame(rows)
    for col in ("_measurement", "_field", "target", "interface"):
        if col not in df:
            df[col] = ""
    df["value"] = pd.to_numeric(df["_value"], errors="coerce")
    when = pd.to_datetime(df["_time"], utc=True, errors="coerce")
    df = df[when.notna() & df["value"].notna()].copy()
    if not len(df):
        return empty
    # Whole seconds since the epoch, whatever resolution pandas parsed to.
    epoch = (when[df.index] - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)
    df["bin"] = epoch.astype("int64") // bins.BIN_S * bins.BIN_S
    df["target"] = df["target"].fillna("").astype(str)
    m, f, v = df["_measurement"], df["_field"], df["value"]
    seconds = m.isin(["tcp_latency", "http_latency", "dns_latency"])
    layer = m.map({"tcp_latency": "tcp", "http_latency": "http", "dns_latency": "dns",
                   "dns_resolution": "dns", "cpe_latency": "hop"})
    parts = []
    # Latency: seconds to ms, except the gateway's (ms already).
    lat = (seconds & (f == "median")) | ((m == "dns_resolution") & (f == "p50")) \
        | ((m == "cpe_latency") & (f == "median") & (v > 0))
    ms = v.where(m == "cpe_latency", v * 1000.0)
    probe = df["target"].where(m != "dns_resolution", "home")
    parts.append(pd.DataFrame({"bin": df["bin"][lat], "layer": layer[lat],
                               "probe": probe[lat], "med": ms[lat], "loss": np.nan}))
    # Loss: ratios to percent, except the gateway's (percent already).
    lo = (seconds | (m == "cpe_latency")) & (f == "loss")
    pct = v.clip(0.0, 1.0).mul(100.0).where(m != "cpe_latency", v.clip(0.0, 100.0))
    parts.append(pd.DataFrame({"bin": df["bin"][lo], "layer": layer[lo],
                               "probe": probe[lo], "med": np.nan, "loss": pct[lo]}))
    table = pd.concat(parts, ignore_index=True)
    if len(table):
        table = (table.groupby(["bin", "layer", "probe"], sort=False)
                 .agg(med=("med", "median"), loss=("loss", "mean")).reset_index())
    wifi = df[m == "wifi_link"]
    if len(wifi):
        w = wifi.pivot_table(index=["bin", "interface"], columns="_field", values="value",
                             aggfunc="mean").reset_index()
        if "uplink" in w:
            w = w[w["uplink"] >= 0.5]
            # A bin where two interfaces both carried the route says nothing.
            w = w[~w["bin"].duplicated(keep=False)]
            for field in ("signal_dbm", "tx_bitrate_mbps"):
                if field in w:
                    x = w[w[field].notna()]
                    table = pd.concat([table, pd.DataFrame(
                        {"bin": x["bin"], "layer": "wifi", "probe": field,
                         "med": x[field], "loss": np.nan})], ignore_index=True)
    return table.reindex(columns=TRIAGE_COLUMNS).astype({"bin": "int64"})


def triage_bins(targets: list[str], start: int, now: int, query=query_influx,
                directory=None) -> pd.DataFrame:
    """Every layer the triage reads but ICMP, as bins since ``start``,
    closed ones from the state volume (bins.py). Each pass reads the last
    TRIAGE_TAIL_S again, which covers every point the exporter may still
    backfill (it never reaches back more than 24 h), so the full read of
    the window (5.7 s on the staging Pi, 12 days, 7 destinations) is a
    weekly safety net rather than a daily cost. The cache is per set of
    targets, so a target added later has its TCP and HTTP history from its
    first pass."""
    import hashlib

    key = hashlib.sha256(",".join(sorted(targets)).encode()).hexdigest()[:8]

    def fetch(since: int, stop: int) -> pd.DataFrame:
        rows = []
        for flux in _triage_flux(targets, since, stop):
            rows.extend(query(flux))
        table = triage_table(rows)
        return table[(table["bin"] >= since) & (table["bin"] < stop)]

    out = bins.read(f"triage-{key}", start, now, fetch, TRIAGE_COLUMNS, directory,
                    text=("layer", "probe"), tail_s=TRIAGE_TAIL_S, refresh_s=TRIAGE_REFRESH_S)
    # The cache of an earlier set of targets is not read again: remove it.
    for old in (directory or bins.state_dir()).glob("triage-*"):
        if not old.name.startswith(f"triage-{key}."):
            try:
                old.unlink()
            except OSError:
                pass
    return out


def _truthy(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1")
    return bool(value)


def path_traces(targets: list[str], start: int, now: int,
                query=query_influx) -> dict[str, list[dict]]:
    """pathmeter's traces to each target since ``start``: ``time``,
    ``changed``, ``as_changed`` and ``as_path``. One query for every
    target; a host without the path meter has none."""
    names = [t for t in targets if SAFE_NAME.fullmatch(t)]
    if not names:
        return {}
    flux = (
        f"from(bucket: {flux_str(influx_bucket())}) "
        f"|> range(start: {int(start)}, stop: {int(now) + 1}) "
        '|> filter(fn: (r) => r._measurement == "path_trace" and '
        f'r.target =~ /^({"|".join(names)})$/) '
        # Traces taken during a loss episode (tagged ``trigger``) are left
        # out, as in segments.py: they lose hops, so a truncated AS path
        # would read as a detour, and they would fill the gaps that say
        # the hourly traces stopped.
        '|> filter(fn: (r) => not exists r.trigger) '
        '|> filter(fn: (r) => r._field == "changed" or r._field == "as_changed" '
        'or r._field == "as_path") '
        '|> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value") '
        '|> keep(columns: ["_time", "target", "changed", "as_changed", "as_path"])'
    )
    out: dict[str, list[dict]] = {}
    for r in query(flux):
        when, target = _epoch_of(r.get("_time")), r.get("target")
        if when is None or not target:
            continue
        out.setdefault(target, []).append({
            "time": when, "changed": _truthy(r.get("changed")),
            "as_changed": _truthy(r.get("as_changed")),
            "as_path": str(r.get("as_path") or "")})
    return out
