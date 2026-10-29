#!/usr/bin/env python3
"""
SmokePing RRD → InfluxDB exporter

∙ Exports normal ping targets to measurement **latency**
∙ Exports DNS-probe targets (under RRD_DIR/resolvers/ or RRD_DIR/DNS_Resolvers/)
  to measurement **dns_latency**
∙ Uses `rrdtool fetch AVERAGE` from the last exported timestamp per RRD, so
  gaps are backfilled after downtime (capped at 24 h) and points are not
  rewritten every cycle. Last-exported timestamps persist in a JSON state
  file (EXPORTER_STATE_FILE, default /tmp/rrd2influx_state.json).

LOSS SEMANTICS (changed 2026-07): the RRD `loss` data source is the raw COUNT
of lost pings per cycle. This exporter converts it to a RATIO 0..1 before
writing, so the dashboards' `percentunit` axes are correct. The field name
stays `loss`.

The denominator is the probe's pings-per-cycle, read PER RRD from its
`ping1..pingN` data sources — probes differ (FPing uses 10, DNS uses 5), and
a fixed denominator understates loss on every probe that disagrees with it.
SMOKEPING_PINGS (default 20) is only a fallback for RRDs with no ping DSs.

CADENCE FIELDS (added 2026-09): every point also carries ``pings`` (the
denominator above) and ``step`` (seconds between the RRD's rows), so the
alerter and the MCP server read each target's real cycle instead of assuming
10 pings every 300 s. They live on the point, not in a side table, so a point
written before a probe's step changed still says what it was measured with.
No dashboard or tool query reads them: all filter on ``_field``, and
``/ping[0-9]+/`` does not match ``pings``.
Historical points written by older versions keep their original scale: before
2026-07 a raw count, and between 2026-07 and 2026-08 a ratio against a fixed
20 (so half the true value for FPing targets, a quarter for DNS).

TARGET HOSTS (added 2026-10): measurement **target_host** says which host
stood behind each target, from SmokePing's Targets file: tags ``target`` and
``category``, string fields ``host`` and ``title``. A point is written when
a target is first seen by this process and whenever its host or title
changes, never per cycle. It exists so a target's name can stay put while
what it points at moves (the Netflix OCA slots, #275): a tag on the latency
points would have split the series again at every change.

TARGET LABELS (added 2026-10): measurement **target_labels** says what
service each target measures (docs/taxonomy.md), for the same reason and in
the same way: tag ``target``, string fields ``service``, ``categories``
(the vocabulary ids, sorted and comma-joined), ``label_source``, and
integer ``taxonomy_version``, written when a target is first seen by this
process and whenever any of those changes. The labels are config-manager's
(``GET /taxonomy/targets``, which resolves them in either mode); this
process never resolves one. When config-manager cannot be asked, nothing is
written and the next round tries again.

The same points carry the delivery axis: string fields ``operator`` and
``kind`` and integer ``asn`` (0 when unknown), who runs the network the
target's host answers from. These this process does compute, from the
host config-manager reports, with ``common/operators.py`` (DNS, then the
origin AS from the routing table in ``<SMOKEPING_CONFIG_DIR>/routing/rrd2influx``,
looked up locally, then ``common/asn.py``'s classification),
cached next to the state file and bounded per round. A host with no IPv4
answer is ``unknown``, an address on the LAN ``local``; neither is an
error, and a later answer is a change like any other.
"""

import glob
import http.client
import json
import logging
import math
import os
import pathlib
import re
import subprocess
import time
import urllib.error
import urllib.request

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

try:
    # Baked into the image at /opt/smoking-pi (shared/modules/smokeping/
    # Dockerfile). An image built before that has no delivery axis.
    from common import asn as routing
    from common.operators import Operators
except ImportError:  # pragma: no cover -- an older image under a newer checkout
    Operators = routing = None

REQUIRED_ENV = ("INFLUX_URL", "INFLUX_TOKEN", "INFLUX_ORG", "INFLUX_BUCKET")
DEFAULT_STATE_FILE = "/tmp/rrd2influx_state.json"
FIRST_RUN_LOOKBACK = 3600       # first run: start from now - 1 h
MAX_BACKFILL = 24 * 3600        # never backfill more than 24 h
WRITE_RETRIES = 3

# Directories that hold DNS-probe RRDs (measurement dns_latency).
DNS_DIRS = ("resolvers", "DNS_Resolvers")
# ... HTTP fetch RRDs (Curl probes, one per HTTP version; measurement
# http_latency) and TCP handshake RRDs (TCPPing; measurement tcp_latency).
# These are the sections config_generator.CATEGORY_PRESENTATION emits.
HTTP_DIRS = ("HTTP",)
TCP_DIRS = ("TCP",)
# The DNS wizard's section holds every protocol; the target name's suffix
# says which (config-manager/wizard_adopt.py names them W_<service>_<proto>).
WIZARD_DIRS = ("DNS_Wizard",)
WIZARD_SUFFIXES = {"_h1": "http1", "_h2": "http2", "_h3": "http3",
                   "_tcp": "tcpping", "_icmp": "fping"}

# HTTP target names end in the version they were probed with
# (Google_h1, Google_h2, Google_h3); that suffix is the probe_type tag.
HTTP_VERSION_SUFFIXES = {"_h1": "http1", "_h2": "http2", "_h3": "http3"}

# SmokePing's per-ping data sources: ping1, ping2, ... one per ping sent.
PING_DS_RE = re.compile(r"^ping\d+$")

# Directory → category tag. Current names are what smokeping_targets.j2
# generates (websites, Netflix, DNS_Resolvers, Custom); legacy names are kept
# so old RRD trees keep exporting with the same tags.
CATEGORY_MAP = {
    "websites": "topsites",
    "Netflix": "netflix",
    "DNS_Resolvers": "dns",
    "Custom": "custom",
    "HTTP": "http",
    "TCP": "tcp",
    "DNS_Wizard": "dns_wizard",
    # cpe_discovery.py's own section (CPE_Targets, @included by Targets):
    # the ISP gateway over ICMP. Unmapped, it charted as "unknown".
    "CPE": "cpe",
    # legacy directory names
    "TopSites": "topsites",
    "resolvers": "dns",
}

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")


# ───────────────────────── classification ─────────────────────────
def measurement_for(rrd_file: str, rrd_dir: str) -> str:
    """Measurement by top-level directory: dns_latency, http_latency,
    tcp_latency, and latency (ICMP) for everything else."""
    rel = pathlib.Path(rrd_file).relative_to(rrd_dir)
    top = rel.parts[0]
    if top in DNS_DIRS:
        return "dns_latency"
    if top in HTTP_DIRS:
        return "http_latency"
    if top in TCP_DIRS:
        return "tcp_latency"
    if top in WIZARD_DIRS:
        name = rel.stem
        if name.endswith(("_h1", "_h2", "_h3")):
            return "http_latency"
        if name.endswith("_tcp"):
            return "tcp_latency"
    return "latency"


def category_for(rrd_file: str, rrd_dir: str) -> str:
    """Map the first-level directory to a category tag (unknown if unmapped)."""
    rel = pathlib.Path(rrd_file).relative_to(rrd_dir)
    directory = rel.parts[0] if len(rel.parts) > 1 else "unknown"
    return CATEGORY_MAP.get(directory, "unknown")


def probe_type_for(rrd_file: str, rrd_dir: str) -> str:
    """dns for DNS measurements; http1/http2/http3 for HTTP targets by name
    suffix (http when unsuffixed); tcpping for TCP; fping6 for IPv6 targets
    (name ends in '6', e.g. Google6); fping otherwise."""
    measurement = measurement_for(rrd_file, rrd_dir)
    target_name = pathlib.Path(rrd_file).stem
    rel = pathlib.Path(rrd_file).relative_to(rrd_dir)
    if rel.parts[0] in WIZARD_DIRS:
        for suffix, probe_type in WIZARD_SUFFIXES.items():
            if target_name.endswith(suffix):
                return probe_type
    if measurement == "dns_latency":
        return "dns"
    if measurement == "http_latency":
        for suffix, probe_type in HTTP_VERSION_SUFFIXES.items():
            if target_name.endswith(suffix):
                return probe_type
        return "http"
    if measurement == "tcp_latency":
        return "tcpping"
    return "fping6" if target_name.endswith("6") else "fping"


# ───────────────────────── loss conversion ─────────────────────────
def loss_to_ratio(loss_count, pings: int):
    """Convert the RRD loss COUNT (0..pings lost pings per cycle) to a ratio
    0..1. Returns None for unknown values or a nonsensical ping count."""
    if loss_count is None or pings <= 0:
        return None
    return max(0.0, min(1.0, float(loss_count) / float(pings)))


def step_from_rows(rows):
    """Seconds between an RRD's rows, or None with fewer than two rows.

    `rrdtool fetch` returns the finest archive that covers the range, whose
    rows are the probe's step apart -- nan rows included, so a fetch of a
    target that is down still has its spacing. The smallest positive gap is
    used so a missing row cannot double it."""
    stamps = sorted({ts for ts, _ in rows})
    gaps = [b - a for a, b in zip(stamps, stamps[1:]) if b > a]
    return min(gaps) if gaps else None


def pings_from_ds_names(ds_names, fallback: int) -> int:
    """Pings per cycle for one RRD, counted from its ping1..pingN sources.

    SmokePing creates one `pingN` data source per ping the probe sends, so
    the RRD is authoritative about its own denominator. Falls back to
    ``fallback`` when an RRD carries no ping sources."""
    count = sum(1 for name in ds_names if PING_DS_RE.match(name))
    return count or fallback


# ───────────────────────── rrdtool fetch ─────────────────────────
def parse_fetch_output(text: str):
    """Parse `rrdtool fetch <rrd> AVERAGE` output.

    Expected shape:

                            median   loss   ping1 ...
        <blank line>
        1690000000: 1.23e-02 0.0e+00 ...
        1690000300: nan nan nan ...

    Returns (ds_names, rows) where rows is a list of
    (timestamp, {ds_name: float | None}); nan/unparseable values become None.
    """
    ds_names = []
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" not in line:
            ds_names = line.split()
            continue
        ts_str, _, values_str = line.partition(":")
        try:
            ts = int(ts_str.strip())
        except ValueError:
            continue
        values = values_str.split()
        row = {}
        for i, name in enumerate(ds_names):
            raw = values[i] if i < len(values) else "nan"
            try:
                val = float(raw)
            except ValueError:
                val = float("nan")
            row[name] = None if math.isnan(val) else val
        rows.append((ts, row))
    return ds_names, rows


def fetch_rows(rrd_path: str, start: int, end: int):
    """Run rrdtool fetch and return parsed rows (see parse_fetch_output)."""
    result = subprocess.run(
        ["rrdtool", "fetch", rrd_path, "AVERAGE",
         "--start", str(start), "--end", str(end)],
        capture_output=True, text=True, check=True,
    )
    return parse_fetch_output(result.stdout)


# ───────────────────────── state file ─────────────────────────
def load_state(path: str) -> dict:
    """Load {rrd_path: last_exported_ts}. Missing/corrupt file → empty dict."""
    try:
        with open(path) as fh:
            state = json.load(fh)
        if isinstance(state, dict):
            return {str(k): int(v) for k, v in state.items()}
        logging.warning("State file %s has unexpected shape, ignoring", path)
    except FileNotFoundError:
        pass
    except (ValueError, TypeError, OSError) as exc:
        logging.warning("Could not read state file %s: %s", path, exc)
    return {}


def save_state(path: str, state: dict) -> None:
    """Atomically persist the state file (write temp file, then rename)."""
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w") as fh:
            json.dump(state, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        logging.error("Could not write state file %s: %s", path, exc)


# ───────────────────────── influx write ─────────────────────────
def write_with_retry(write_api, bucket: str, points, retries: int = WRITE_RETRIES) -> bool:
    """Write points with exponential backoff (1 s, 2 s, 4 s). Returns False
    after the final attempt; the caller then leaves the state untouched so the
    same window is re-fetched next cycle (bounded by MAX_BACKFILL)."""
    delay = 1.0
    for attempt in range(1, retries + 1):
        try:
            write_api.write(bucket=bucket, record=points)
            return True
        except Exception as exc:
            if attempt < retries:
                logging.warning("Influx write failed (attempt %d/%d): %s — retrying in %.0fs",
                                attempt, retries, exc, delay)
                time.sleep(delay)
                delay *= 2
            else:
                logging.error("Influx write failed after %d attempts, dropping batch of %d points: %s",
                              retries, len(points), exc)
    return False


# ───────────────────────── target hosts ─────────────────────────
SMOKEPING_CONFIG_DIR = os.getenv("SMOKEPING_CONFIG_DIR", "/config")
TARGETS_FILE = "Targets"
SECTION_RE = re.compile(r"^(\+{1,})\s*(\S+)")
INCLUDE_RE = re.compile(r"^@include\s+(\S+)")
SETTING_RE = re.compile(r"^(host|title)\s*=\s*(.*?)\s*$")


def parse_targets(path: str, _seen=None) -> dict:
    """{target name: {"host", "title", "category"}} from a SmokePing Targets
    file and the files it @includes (CPE_Targets). A target is a section
    with a host; its category is the top-level section's, mapped the way
    the RRD directories are (CATEGORY_MAP). A missing file is no targets."""
    seen = _seen if _seen is not None else set()
    real = os.path.realpath(path)
    if real in seen:
        return {}
    seen.add(real)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return {}
    targets = {}
    top = None
    node = None
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        inc = INCLUDE_RE.match(line)
        if inc:
            child = inc.group(1)
            if not os.path.isabs(child):
                child = os.path.join(os.path.dirname(path), child)
            targets.update(parse_targets(child, seen))
            continue
        sec = SECTION_RE.match(line)
        if sec:
            depth, name = len(sec.group(1)), sec.group(2)
            if depth == 1:
                top, node = name, None
            else:
                node = {"name": name, "category": CATEGORY_MAP.get(top, "unknown")}
            continue
        setting = SETTING_RE.match(line)
        if setting and node is not None:
            node[setting.group(1)] = setting.group(2)
            if "host" in node:
                targets[node["name"]] = {
                    "host": node["host"], "title": node.get("title", ""),
                    "category": node["category"]}
    return targets


def host_points(targets: dict, known: dict, now: int):
    """target_host points for the targets new to ``known`` or changed since,
    and ``known`` updated to match. Targets that disappeared are forgotten,
    so one that comes back is written again."""
    points = []
    for name, info in sorted(targets.items()):
        current = (info["host"], info["title"])
        if known.get(name) == current:
            continue
        known[name] = current
        points.append(Point("target_host")
                      .tag("target", name)
                      .tag("category", info["category"])
                      .field("host", info["host"])
                      .field("title", info["title"])
                      .time(now, WritePrecision.S))
    for gone in [n for n in known if n not in targets]:
        del known[gone]
    return points


def record_hosts(write_api, bucket: str, known: dict, now: int,
                 config_dir: str = SMOKEPING_CONFIG_DIR) -> int:
    """Write target_host points for what changed; returns how many. On a
    failed write ``known`` is put back, so the next cycle tries again."""
    before = dict(known)
    points = host_points(parse_targets(os.path.join(config_dir, TARGETS_FILE)), known, now)
    if points and not write_with_retry(write_api, bucket, points):
        known.clear()
        known.update(before)
        return 0
    return len(points)


# ───────────────────────── target labels ─────────────────────────
DEFAULT_CONFIG_API_URL = "http://127.0.0.1:5000"
# config-manager resolves every target on each request; labels change when
# someone edits a target, an override or the shipped rules, never per cycle.
LABELS_INTERVAL = 300


def fetch_labels(base_url: str, token: str = "",
                 opener=urllib.request.urlopen) -> dict | None:
    """config-manager's ``GET /taxonomy/targets`` body, or None when it
    cannot be had. Only the status or the exception's type is logged: a
    URL error can carry the address, and nothing here echoes the token."""
    req = urllib.request.Request(base_url.rstrip("/") + "/taxonomy/targets")
    if token:
        req.add_header("X-API-Token", token)
    try:
        # Short: this runs inside the export loop, which waits for it.
        with opener(req, timeout=10) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        logging.warning("target_labels: not read this round: HTTP %s", exc.code)
        return None
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as exc:
        logging.warning("target_labels: not read this round: %s", type(exc).__name__)
        return None
    if not isinstance(body, dict) or not isinstance(body.get("targets"), list):
        logging.warning("target_labels: not read this round: unexpected answer")
        return None
    return body


UNKNOWN_DELIVERY = ("unknown", "unknown", 0)


def label_points(body: dict, known: dict, now: int, delivery=None):
    """target_labels points for the active targets new to ``known`` or
    relabeled since, and ``known`` updated to match. ``delivery(name)`` is
    the target's (operator, kind, asn); without it every target is
    unknown. Targets that disappeared (or were deactivated) are
    forgotten, so one that comes back is written again."""
    try:
        version = int(body.get("version") or 0)
    except (TypeError, ValueError):
        version = 0
    points = []
    seen = set()
    for entry in sorted((t for t in body.get("targets") or [] if isinstance(t, dict)),
                        key=lambda t: str(t.get("name") or "")):
        name = entry.get("name")
        if not name or entry.get("is_active") is False or name in seen:
            continue
        seen.add(name)
        categories = ",".join(sorted(str(c) for c in entry.get("categories") or []))
        operator, kind, asn = delivery(name) if delivery else UNKNOWN_DELIVERY
        current = (str(entry.get("service") or ""), categories,
                   str(entry.get("label_source") or ""), version,
                   str(operator), str(kind), int(asn or 0))
        if known.get(name) == current:
            continue
        known[name] = current
        points.append(Point("target_labels")
                      .tag("target", name)
                      .field("service", current[0])
                      .field("categories", current[1])
                      .field("label_source", current[2])
                      .field("taxonomy_version", version)
                      .field("operator", current[4])
                      .field("kind", current[5])
                      .field("asn", current[6])
                      .time(now, WritePrecision.S))
    # An empty list is a config-manager without targets for a moment (a
    # restore, a migration), not every target gone: forgetting them all
    # would rewrite every label on the next round.
    if seen:
        for gone in [n for n in known if n not in seen]:
            del known[gone]
    return points


def active_hosts(body: dict) -> dict:
    """target -> host for the active targets in a ``/taxonomy/targets`` body."""
    return {str(t["name"]): str(t.get("host") or "")
            for t in body.get("targets") or []
            if isinstance(t, dict) and t.get("name") and t.get("is_active") is not False}


def record_labels(write_api, bucket: str, known: dict, now: int,
                  fetch=None, operators=None) -> int:
    """Write target_labels points for what changed; returns how many. When
    config-manager cannot be asked, nothing changes; on a failed write
    ``known`` is put back, so the next round tries again. ``operators``
    (a ``common.operators.Operators``) looks up who delivers each target;
    its lookups failing leaves those targets ``unknown``, never the round."""
    if fetch is None:
        def fetch():
            return fetch_labels(os.getenv("CONFIG_API_URL") or DEFAULT_CONFIG_API_URL,
                                os.getenv("CONFIG_API_TOKEN", ""))
    body = fetch()
    if body is None:
        return 0
    delivery = None
    if operators is not None:
        hosts = active_hosts(body)
        try:
            operators.refresh(hosts)
        except Exception as exc:  # noqa: BLE001 -- the labels must not wait on the lookups
            logging.warning("target_labels: delivery lookups failed: %s", type(exc).__name__)

        def delivery(name):
            return operators.owner(name, hosts)
    before = dict(known)
    points = label_points(body, known, now, delivery)
    if points and not write_with_retry(write_api, bucket, points):
        known.clear()
        known.update(before)
        return 0
    return len(points)


# ───────────────────────── export ─────────────────────────
def build_points(rrd_file: str, rows, rrd_dir: str, pings: int, step=None):
    """Turn fetch rows into Influx points, timestamped from the RRD rows.
    Returns (points, last_ts) where last_ts is the newest row with data.

    ``pings`` and ``step`` (when known) are written as fields on every
    point; see CADENCE FIELDS in the module docstring."""
    measurement = measurement_for(rrd_file, rrd_dir)
    target_name = pathlib.Path(rrd_file).stem
    category = category_for(rrd_file, rrd_dir)
    probe_type = probe_type_for(rrd_file, rrd_dir)

    points = []
    last_ts = None
    for ts, data in rows:
        fields = {}
        for name, val in data.items():
            if val is None:
                continue
            if name == "loss":
                val = loss_to_ratio(val, pings)
                if val is None:
                    continue
            fields[name] = float(val)   # latency stays raw seconds (×1000 in dashboards)
        if not fields:
            continue                    # skip all-NaN rows (e.g. in-progress bucket)
        pt = (Point(measurement)
              .tag("target", target_name)
              .tag("category", category)
              .tag("probe_type", probe_type)
              .time(ts, WritePrecision.S))
        for name, val in fields.items():
            pt.field(name, val)
        pt.field("pings", int(pings))
        if step:
            pt.field("step", int(step))
        points.append(pt)
        last_ts = ts
    return points, last_ts


def _rrd_changed_since(rrd_path: str, last_exported_ts: int) -> bool:
    """Whether an RRD has been written since we last exported from it.

    RRD updates rewrite the file, so mtime advancing is the cheap signal that
    new data exists. Unreadable mtime means "assume changed" — a stat error
    must never silently stop exporting a target.
    """
    try:
        return os.path.getmtime(rrd_path) > last_exported_ts
    except OSError:
        return True


def run_cycle(write_api, bucket: str, rrd_dir: str, state: dict, pings: int) -> None:
    now = int(time.time())
    rrd_files = glob.glob(os.path.join(rrd_dir, "**", "*.rrd"), recursive=True)
    logging.info("Processing %d RRD files...", len(rrd_files))

    # Drop state entries for RRDs that no longer exist.
    current = set(rrd_files)
    for stale in [k for k in state if k not in current]:
        del state[stale]

    exported = 0
    for rrd in rrd_files:
        try:
            start = state.get(rrd, now - FIRST_RUN_LOOKBACK)
            start = max(start, now - MAX_BACKFILL)

            # SmokePing writes on a 300 s step but this loop runs every 60 s,
            # so most cycles have nothing new: spawning `rrdtool fetch` for
            # every RRD anyway cost ~40 processes a minute on a Pi that was
            # thermally throttling. Skip the ones whose file has not been
            # touched since we last exported them. Only skip RRDs we have
            # already exported at least once, so first runs and backfills are
            # unaffected.
            if rrd in state and not _rrd_changed_since(rrd, state[rrd]):
                continue

            ds_names, rows = fetch_rows(rrd, start, now)
            step = step_from_rows(rows)
            rows = [(ts, data) for ts, data in rows if start < ts <= now]
            points, last_ts = build_points(
                rrd, rows, rrd_dir, pings_from_ds_names(ds_names, pings), step)
            if not points:
                continue
            if write_with_retry(write_api, bucket, points):
                state[rrd] = last_ts
                exported += len(points)
        except subprocess.CalledProcessError as exc:
            logging.warning("%s → rrdtool fetch failed: %s", rrd, exc.stderr)
        except Exception as exc:
            logging.warning("%s → %s", rrd, exc)
    logging.info("Cycle complete: %d points exported", exported)


def main() -> int:
    missing = [name for name in REQUIRED_ENV if not os.getenv(name)]
    if missing:
        logging.error("Missing required environment variables: %s",
                      ", ".join(missing))
        return 1

    influx_url = os.getenv("INFLUX_URL")
    influx_token = os.getenv("INFLUX_TOKEN")
    influx_org = os.getenv("INFLUX_ORG")
    influx_bucket = os.getenv("INFLUX_BUCKET")
    rrd_dir = os.getenv("RRD_DIR", "/var/lib/smokeping")
    interval = int(os.getenv("EXPORT_INTERVAL", "60"))
    pings = int(os.getenv("SMOKEPING_PINGS", "20"))
    state_file = os.getenv("EXPORTER_STATE_FILE", DEFAULT_STATE_FILE)

    client = InfluxDBClient(url=influx_url, token=influx_token,
                            org=influx_org, timeout=10000)
    write_api = client.write_api(write_options=SYNCHRONOUS)
    state = load_state(state_file)
    # In memory on purpose: a restart writes every target's host once more,
    # which is a few hundred points and no harm.
    hosts = {}
    labels = {}
    labels_due = 0.0
    operators = (Operators(pathlib.Path(state_file).with_name("rrd2influx_operators.json"))
                 if Operators is not None else None)
    table = (routing.use_table(pathlib.Path(SMOKEPING_CONFIG_DIR) / "routing" / "rrd2influx")
             if routing is not None else None)
    if operators is None:
        logging.warning("target_labels: common/ is not in this image (built before the "
                        "delivery axis): every target's operator and kind stay unknown "
                        "until the smokeping image is rebuilt")

    logging.info("RRD exporter started → %s (bucket: %s, pings/cycle: per-RRD "
                 "(fallback %d), state: %s)",
                 influx_url, influx_bucket, pings, state_file)
    try:
        while True:
            try:
                run_cycle(write_api, influx_bucket, rrd_dir, state, pings)
                save_state(state_file, state)
                changed = record_hosts(write_api, influx_bucket, hosts, int(time.time()))
                if changed:
                    logging.info("target_host: %d target(s) new or moved", changed)
                if time.time() >= labels_due:
                    labels_due = time.time() + LABELS_INTERVAL
                    if table is not None:
                        table.refresh()  # never raises; owners unknown until it loads
                    changed = record_labels(write_api, influx_bucket, labels,
                                            int(time.time()), operators=operators)
                    if changed:
                        logging.info("target_labels: %d target(s) new or relabeled",
                                     changed)
            except Exception as exc:
                logging.error("Main loop error: %s", exc)
            time.sleep(interval)
    except KeyboardInterrupt:
        logging.info("Received interrupt signal, shutting down")
        save_state(state_file, state)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
