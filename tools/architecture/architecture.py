"""The architecture diagram: one spec, two drawings.

    python tools/architecture/architecture.py          # write both files
    python tools/architecture/architecture.py --check  # fail if they are stale

Writes ``docs/img/architecture.svg`` (shown in docs/architecture.md) and
``docs/architecture.excalidraw`` (open it at excalidraw.com to edit the
drawing by hand). Edit NODES and EDGES here, not the outputs: the tests in
tools/architecture/tests fail when a Compose service, a profile or an
exporter the SmokePing container starts is missing from the spec, or when
the committed drawings differ from what this script writes.

The drawing: a Raspberry Pi boundary holds four columns, Configure, Measure,
Store and Look and act; what the Pi talks to sits outside it. Arrows are
routed on a grid (horizontal and vertical runs, rounded corners) from a
side of one box to a side of another, so a new box needs coordinates and
sides, never hand-placed bends.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from html import escape
from itertools import pairwise
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SVG_OUT = ROOT / "docs/img/architecture.svg"
EXCALIDRAW_OUT = ROOT / "docs/architecture.excalidraw"

WIDTH, HEIGHT = 1920, 1120
FONT = "Inter, ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, 'SF Mono', Menlo, Consolas, monospace"

INK = "#0f172a"        # titles
TEXT = "#1f2937"       # body text
MUTED = "#64748b"      # headers, captions
LINE = "#334155"       # arrows and their labels
HAIRLINE = "#cbd5e1"   # column rules, pill borders

# kind -> (stroke, fill)
STYLE = {
    "core": ("#1e40af", "#eff6ff"),
    "optional": ("#c2410c", "#fff7ed"),
    "store": ("#047857", "#ecfdf5"),
    "external": ("#64748b", "#f8fafc"),
}


@dataclass(frozen=True)
class Node:
    id: str
    title: str
    lines: tuple[str, ...]  # a line starting with "· " is a smaller, bulleted detail
    x: int
    y: int
    w: int
    h: int
    kind: str  # core | optional | store | external
    icon: str = ""  # a symbol id from ICONS
    # Compose services this box stands for, and the profile that turns them
    # on (None: always on). The tests hold both to the Compose files.
    services: tuple[str, ...] = ()
    profile: str | None = None
    # Scripts the SmokePing container starts (custom-cont-init.d).
    exporters: tuple[str, ...] = ()
    # Where you come in: a badge such as "you · :8080".
    badge: str | None = None


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    label: str
    src_side: str  # l r t b
    dst_side: str
    # Where on the side: a fraction of it (<= 1), or an absolute x/y (> 1).
    src_at: float = 0.5
    dst_at: float = 0.5
    # The coordinate of the middle run, when the route has one (a Z or a U).
    bend: float | None = None
    both: bool = False
    dashed: bool = False
    label_at: tuple[float, float] | None = None  # default: next to the longest run


@dataclass(frozen=True)
class Column:
    title: str
    x: int
    w: int


TITLE = "Smoking Pi"
SUBTITLE = ("How the Pro edition fits together: every container, what it reads and writes,",
            ("and what sits outside the Pi. Basic runs SmokePing and mdns; Standard adds the "
             "Configure column."))

PI = (40, 230, 1560, 720)  # the Raspberry Pi boundary: x, y, w, h
PI_TITLE = "RASPBERRY PI"
PI_NOTE = "Docker Compose, run by the smoking-pi command"
COLUMNS = (
    Column("CONFIGURE", 70, 260),
    Column("MEASURE", 430, 480),
    Column("STORE", 960, 240),
    Column("LOOK AND ACT", 1260, 300),
)

NODES: tuple[Node, ...] = (
    # Outside the Pi
    Node("internet", "The Internet: what is measured",
         ("sites and CDNs, Netflix OCAs, public resolvers, and the router",),
         430, 120, 480, 76, "external", icon="globe"),
    Node("ipinfo", "IPinfo",
         ("the public address's city, once a day;", "PUBLIC_IP_GEO=0 turns it off"),
         960, 110, 320, 90, "external", icon="globe"),
    Node("router", "Home router",
         ("forwards the house's DNS", "to the Pi (optional)"),
         70, 1000, 260, 90, "external", icon="wifi"),
    Node("doh", "Encrypted DNS upstreams",
         ("DoH to 1.1.1.1 and 8.8.8.8;", "plain DNS as the last resort"),
         960, 1000, 320, 90, "external", icon="lock"),
    Node("chat", "Chat and webhooks",
         ("Telegram through OpenClaw,", "Slack, webhooks"),
         1650, 470, 240, 120, "external", icon="chat"),
    Node("anthropic", "Anthropic API", ("writes the health reports",),
         1650, 630, 240, 110, "external", icon="cloud"),
    Node("assistants", "AI assistants",
         ("OpenClaw, any MCP client;", "charts go back to the chat"),
         1650, 790, 240, 120, "external", icon="bot"),
    # Configure
    Node("web-admin", "web-admin",
         ("Flask UI: targets, probes,", "the tour, the AI reports"),
         70, 330, 260, 120, "core", icon="sliders", services=("web-admin",),
         badge="you · :8080"),
    Node("config-manager", "config-manager",
         ("REST API :5000", "writes SmokePing's config"),
         70, 530, 260, 120, "core", icon="doc", services=("config-manager",)),
    Node("postgres", "PostgreSQL",
         ("targets, categories,", "probes, sources"),
         70, 730, 260, 120, "store", icon="db", services=("postgres",)),
    # Measure
    Node("smokeping", "SmokePing", (
        "FPing, FPing6, TCPPing, Curl (HTTP/1.1, /2, /3), DNS",
        "one RRD file per target",
        "",
        "Exporters started in the container:",
        "· rrd2influx / rrd2clickhouse: RRD to the time series",
        "· microcut_detector: outages shorter than a step",
        "· cpe_discovery: the ISP's first hop",
        "· wifi_link: the Wi-Fi link and the uplink",
        "· resolver_identity: which resolver answers",
        "· public_ip: the public address and its network",
        "· dns_wizard: the wizard's snapshot",
        "· measurement_budget: config-manager's budget",
    ), 430, 330, 480, 330, "core", icon="pulse", services=("smokeping",), exporters=(
        "rrd2influx", "rrd2clickhouse", "microcut_detector", "cpe_discovery",
        "wifi_link", "resolver_identity", "public_ip", "dns_wizard", "measurement_budget",
    )),
    Node("mdns", "mdns",
         ("the name smoking-pi.local,", "by multicast DNS"),
         700, 680, 210, 92, "core", icon="wifi", services=("mdns",)),
    Node("dns-observer", "DNS observer",
         ("AdGuard Home on :53, and the wizard", "what the house resolves, by service"),
         430, 790, 480, 120, "optional", icon="search", services=("dns-observer",),
         profile="dns"),
    # Store
    Node("influxdb", "InfluxDB",
         ("time series", "the default backend"),
         960, 330, 240, 400, "store", icon="db", services=("influxdb",), profile="influxdb"),
    Node("clickhouse", "ClickHouse", ("the alternative backend",),
         960, 790, 240, 120, "optional", icon="db", services=("clickhouse",),
         profile="clickhouse"),
    # Look and act
    Node("grafana", "Grafana", ("dashboards; a ClickHouse set too",),
         1260, 330, 300, 100, "core", icon="chart", services=("grafana",),
         badge="you · :3000"),
    Node("alerter", "alerter",
         ("outages, microcuts, verdicts,", "digests, the AI reports"),
         1260, 470, 300, 120, "optional", icon="bell", services=("alerter",),
         profile="alerts"),
    Node("ai-insights", "ai-insights",
         ("health reports, into", "a volume web-admin shows"),
         1260, 630, 300, 110, "optional", icon="sparkle", services=("ai-insights",),
         profile="ai"),
    Node("mcp-server", "mcp-server",
         ("tools for assistants; asks", "config-manager, mutes alerts"),
         1260, 790, 300, 120, "optional", icon="plug", services=("mcp-server",),
         profile="mcp"),
)

EDGES: tuple[Edge, ...] = (
    Edge("web-admin", "config-manager", "REST", "b", "t"),
    Edge("config-manager", "postgres", "SQL", "b", "t", both=True),
    Edge("config-manager", "smokeping", "Targets, Probes", "r", "l", src_at=590, dst_at=590),
    Edge("smokeping", "internet", "ICMP, TCP, HTTP, DNS probes", "t", "b", src_at=670, dst_at=670,
         label_at=(790, 262)),
    Edge("smokeping", "ipinfo", "public address", "t", "b", src_at=890, dst_at=1020,
         bend=255, label_at=(965, 255)),
    Edge("smokeping", "influxdb", "points", "r", "l", src_at=430, dst_at=430),
    Edge("smokeping", "clickhouse", "rows", "r", "l", src_at=620, dst_at=850, bend=935,
         dashed=True, label_at=(935, 735)),
    Edge("grafana", "influxdb", "Flux", "l", "r", src_at=380, dst_at=380),
    Edge("alerter", "influxdb", "reads", "l", "r", src_at=530, dst_at=530),
    Edge("ai-insights", "influxdb", "reads", "l", "r", src_at=685, dst_at=685),
    Edge("mcp-server", "influxdb", "reads", "l", "r", src_at=850, dst_at=705, bend=1230,
         label_at=(1230, 778)),
    Edge("alerter", "chat", "sends", "r", "l", src_at=530, dst_at=530),
    Edge("ai-insights", "anthropic", "asks", "r", "l", src_at=685, dst_at=685),
    Edge("assistants", "mcp-server", "MCP", "l", "r", src_at=850, dst_at=850, both=True),
    Edge("ai-insights", "alerter", "reports", "t", "b", src_at=1410, dst_at=1410),
    Edge("dns-observer", "smokeping", "wizard.json", "t", "b", src_at=670, dst_at=670,
         label_at=(600, 725)),
    Edge("dns-observer", "config-manager", "wizard.json / Targets", "l", "r",
         src_at=850, dst_at=620, bend=380, both=True, label_at=(380, 690)),
    Edge("router", "dns-observer", "the house's DNS", "t", "b", src_at=200, dst_at=550,
         bend=975),
    Edge("dns-observer", "doh", "encrypted upstreams", "b", "l", src_at=790, dst_at=1045,
         label_at=(875, 1030)),
)

LEGEND_X, LEGEND_Y = 1010, 44
LEGEND_BOXES = (
    ("core", "always on"),
    ("optional", "behind a Compose profile"),
    ("store", "a data store"),
    ("external", "outside the Pi"),
)
LEGEND_ARROWS = (
    (False, "reads, writes, calls"),
    (True, "only with the clickhouse profile"),
)

# Small line icons, 24x24, drawn with the box's stroke colour.
ICONS = {
    "sliders": "M4 6h16M4 12h16M4 18h16",
    "sliders_knobs": ((8, 6), (16, 12), (10, 18)),
    "doc": "M6 3h9l4 4v14H6zM15 3v4h4M9 12h6M9 16h6",
    "db": "M5 6c0-1.7 3.1-3 7-3s7 1.3 7 3-3.1 3-7 3-7-1.3-7-3zM5 6v12c0 1.7 3.1 3 7 3s7-1.3 "
          "7-3V6M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3",
    "pulse": "M3 12h4l2-5 4 10 2-5h6",
    "search": "M15 15l5 5M4.5 10.5a6 6 0 1 0 12 0 6 6 0 1 0-12 0",
    "chart": "M4 20v-9M10 20V4M16 20v-7M2 20h20",
    "bell": "M6 16v-5a6 6 0 0 1 12 0v5l2 2H4zM10 21h4",
    "sparkle": "M12 3l2.2 5.8L20 11l-5.8 2.2L12 19l-2.2-5.8L4 11l5.8-2.2z",
    "plug": "M9 3v5M15 3v5M6 8h12v4a6 6 0 0 1-12 0zM12 18v3",
    "globe": "M3 12a9 9 0 1 0 18 0 9 9 0 1 0-18 0M3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 "
             "3.5-3 14.5 0 18",
    "wifi": "M2 9a15 15 0 0 1 20 0M5.5 12.5a10 10 0 0 1 13 0M9 16a5 5 0 0 1 6 0M12 19.5v.1",
    "lock": "M5 11h14v10H5zM8 11V8a4 4 0 0 1 8 0v3",
    "chat": "M4 5h16v11H9l-5 4z",
    "cloud": "M7 18h10a4 4 0 0 0 .5-8A6 6 0 0 0 6 11a3.5 3.5 0 0 0 1 7z",
    "bot": "M4 8h16v12H4zM12 4v4M9 14h.1M15 14h.1",
    "person": "M8 8a4 4 0 1 0 8 0 4 4 0 1 0-8 0M4 21a8 8 0 0 1 16 0",
}


# -- geometry --------------------------------------------------------------------


def node(nid: str) -> Node:
    return next(n for n in NODES if n.id == nid)


def port(n: Node, side: str, at: float, gap: float = 3) -> tuple[float, float]:
    """A point just off one side of a box."""
    if side in "lr":
        y = n.y + n.h * at if at <= 1 else at
        return (n.x - gap if side == "l" else n.x + n.w + gap), y
    x = n.x + n.w * at if at <= 1 else at
    return x, (n.y - gap if side == "t" else n.y + n.h + gap)


def route(e: Edge) -> list[tuple[float, float]]:
    """Horizontal and vertical runs from a side of ``src`` to a side of ``dst``."""
    p0 = port(node(e.src), e.src_side, e.src_at)
    p1 = port(node(e.dst), e.dst_side, e.dst_at)
    horizontal = {"l", "r"}
    s_h, d_h = e.src_side in horizontal, e.dst_side in horizontal
    if s_h and d_h:  # a straight run, a Z, or a U
        if abs(p0[1] - p1[1]) < 1 and e.bend is None:
            return [p0, p1]
        mx = e.bend if e.bend is not None else (p0[0] + p1[0]) / 2
        return [p0, (mx, p0[1]), (mx, p1[1]), p1]
    if not s_h and not d_h:
        if abs(p0[0] - p1[0]) < 1 and e.bend is None:
            return [p0, p1]
        my = e.bend if e.bend is not None else (p0[1] + p1[1]) / 2
        return [p0, (p0[0], my), (p1[0], my), p1]
    if s_h:  # out sideways, in from above or below: one corner
        return [p0, (p1[0], p0[1]), p1]
    return [p0, (p0[0], p1[1]), p1]


def rounded_path(pts: list[tuple[float, float]], r: float = 10) -> str:
    """The polyline as an SVG path, corners rounded."""
    if len(pts) == 2:
        return f"M{pts[0][0]:.0f},{pts[0][1]:.0f} L{pts[1][0]:.0f},{pts[1][1]:.0f}"
    d = [f"M{pts[0][0]:.0f},{pts[0][1]:.0f}"]
    for i in range(1, len(pts) - 1):
        (ax, ay), (bx, by), (cx, cy) = pts[i - 1], pts[i], pts[i + 1]
        r1 = min(r, (abs(bx - ax) + abs(by - ay)) / 2)
        r2 = min(r, (abs(cx - bx) + abs(cy - by)) / 2)
        rr = min(r1, r2, r)
        ix = bx - rr * (1 if bx > ax else -1 if bx < ax else 0)
        iy = by - rr * (1 if by > ay else -1 if by < ay else 0)
        ox = bx + rr * (1 if cx > bx else -1 if cx < bx else 0)
        oy = by + rr * (1 if cy > by else -1 if cy < by else 0)
        d.append(f"L{ix:.1f},{iy:.1f} Q{bx:.0f},{by:.0f} {ox:.1f},{oy:.1f}")
    d.append(f"L{pts[-1][0]:.0f},{pts[-1][1]:.0f}")
    return " ".join(d)


def label_point(e: Edge, pts: list[tuple[float, float]]) -> tuple[float, float]:
    """Beside the longest run: above a horizontal one, right of a vertical one."""
    if e.label_at:
        return e.label_at
    runs = list(pairwise(pts))
    (x1, y1), (x2, y2) = max(runs, key=lambda s: abs(s[1][0] - s[0][0]) + abs(s[1][1] - s[0][1]))
    x, y = (x1 + x2) / 2, (y1 + y2) / 2
    if abs(y2 - y1) < 1:
        return x, y - 15
    return x + 10 + pill_width(e.label) / 2, y


def pill_width(text: str) -> float:
    return 7.2 * len(text) + 16


def title_of(n: Node) -> str:
    return f"{n.title}  [{n.profile}]" if n.profile else n.title


# -- SVG ---------------------------------------------------------------------------


def _icon(icon: str, x: float, y: float, color: str, size: float = 20) -> str:
    s = size / 24
    out = [(f'<g transform="translate({x:.0f},{y:.0f}) scale({s:.3f})" fill="none" '
            f'stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'),
           f'<path d="{ICONS[icon]}"/>']
    if icon == "sliders":
        out += [f'<circle cx="{cx}" cy="{cy}" r="2.4" fill="#ffffff"/>'
                for cx, cy in ICONS["sliders_knobs"]]
    out.append("</g>")
    return "".join(out)


def _pill(x: float, y: float, text: str, *, color: str = LINE, fill: str = "#ffffff",
          border: str = HAIRLINE, size: float = 12.5, italic: bool = True,
          weight: str = "normal") -> str:
    w = pill_width(text) if size <= 13 else 7.8 * len(text) + 18
    style = ' font-style="italic"' if italic else ""
    return (f'<rect x="{x - w / 2:.0f}" y="{y - 11:.0f}" width="{w:.0f}" height="22" rx="11" '
            f'fill="{fill}" stroke="{border}" stroke-width="1"/>'
            f'<text x="{x:.0f}" y="{y + 4.5:.0f}" text-anchor="middle" font-size="{size}" '
            f'font-weight="{weight}"{style} fill="{color}">{escape(text)}</text>')


def _box_shape(n: Node, stroke: str, fill: str, dash: str) -> str:
    if n.kind != "store":
        return (f'<rect x="{n.x}" y="{n.y}" width="{n.w}" height="{n.h}" rx="14" fill="{fill}" '
                f'stroke="{stroke}" stroke-width="2.25"{dash} filter="url(#shadow)"/>')
    # A cylinder: an ellipse on top, the body below it.
    ry = 11
    x, y, w, h = n.x, n.y + ry, n.w, n.h - ry
    body = (f"M{x},{y} v{h - ry} a{w / 2},{ry} 0 0 0 {w},0 v-{h - ry}")
    return (f'<path d="{body}" fill="{fill}" stroke="{stroke}" stroke-width="2.25" '
            f'filter="url(#shadow)"/>'
            f'<ellipse cx="{x + w / 2}" cy="{y}" rx="{w / 2}" ry="{ry}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="2.25"/>')


def _box(n: Node) -> str:
    stroke, fill = STYLE[n.kind]
    dash = ' stroke-dasharray="9 6"' if n.kind == "optional" else ""
    out = [_box_shape(n, stroke, fill, dash)]
    top = n.y + (14 if n.kind == "store" else 0)
    tx = n.x + 18
    if n.icon:
        out.append(_icon(n.icon, tx, top + 15, stroke))
        tx += 30
    title_color = stroke if n.kind != "external" else "#334155"
    out.append(f'<text x="{tx}" y="{top + 31}" font-size="17" font-weight="700" '
               f'fill="{title_color}">{escape(n.title)}</text>')
    if n.profile:
        title_w = 9.5 * len(n.title) + (48 if n.icon else 18)
        fits = title_w + pill_width(n.profile) + 36 <= n.w
        py = top + 25 if fits else n.y + n.h - 22
        out.append(_pill(n.x + n.w - 18 - pill_width(n.profile) / 2, py, n.profile,
                         color=stroke, fill="#ffffff", border=stroke, italic=False,
                         weight="600"))
    if n.badge:
        bw = pill_width(n.badge) + 18
        bx = n.x + n.w - 18 - bw / 2
        by = n.y + n.h - 33
        out.append(f'<rect x="{bx - bw / 2:.0f}" y="{by}" width="{bw:.0f}" height="22" '
                   f'rx="11" fill="{stroke}"/>')
        out.append(_icon("person", bx - bw / 2 + 7, by + 3, "#ffffff", size=16))
        out.append(f'<text x="{bx + 9:.0f}" y="{by + 15.5}" text-anchor="middle" '
                   f'font-size="12" font-weight="600" fill="#ffffff">{escape(n.badge)}</text>')
    y = top + 57
    for line in n.lines:
        if not line:
            y += 8
            continue
        if line.startswith("· "):
            name, _, rest = line[2:].partition(":")
            out.append(f'<text x="{n.x + 24}" y="{y}" font-size="13" fill="{TEXT}">'
                       f'<tspan fill="{MUTED}">•</tspan> <tspan font-family="{MONO}" '
                       f'font-size="12.5">{escape(name)}</tspan>{escape(":" + rest if rest else "")}'
                       '</text>')
            y += 20
            continue
        if line.endswith(":"):
            out.append(f'<text x="{n.x + 18}" y="{y}" font-size="12" font-weight="700" '
                       f'letter-spacing="1" fill="{MUTED}">{escape(line[:-1].upper())}</text>')
            y += 22
            continue
        out.append(f'<text x="{n.x + 18}" y="{y}" font-size="14" fill="{TEXT}">'
                   f'{escape(line)}</text>')
        y += 21
    return "\n".join(out)


def svg() -> str:
    px, py, pw, ph = PI
    out = [
        (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
         f'width="{WIDTH}" height="{HEIGHT}" font-family="{FONT}" role="img" '
         'aria-labelledby="t">'),
        ('<title id="t">Smoking Pi architecture: the containers on the Pi, what each reads '
         'and writes, and what sits outside</title>'),
        "<defs>",
        ('<marker id="arrow" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" '
         'markerHeight="7" orient="auto-start-reverse"><path d="M1,1 L9,5 L1,9 z" '
         f'fill="{LINE}"/></marker>'),
        ('<filter id="shadow" x="-4%" y="-6%" width="108%" height="116%">'
         '<feDropShadow dx="0" dy="1.5" stdDeviation="2" flood-color="#0f172a" '
         'flood-opacity="0.10"/></filter>'),
        "</defs>",
        f'<rect width="{WIDTH}" height="{HEIGHT}" rx="18" fill="#ffffff"/>',
        # Title block
        f'<text x="40" y="60" font-size="28" font-weight="800" fill="{INK}">{escape(TITLE)}</text>',
        f'<text x="40" y="86" font-size="14.5" fill="{MUTED}">{escape(SUBTITLE[0])}</text>',
        f'<text x="40" y="106" font-size="14.5" fill="{MUTED}">{escape(SUBTITLE[1])}</text>',
    ]
    # Legend
    x = LEGEND_X
    for kind, text in LEGEND_BOXES:
        stroke, fill = STYLE[kind]
        dash = ' stroke-dasharray="5 3"' if kind == "optional" else ""
        if kind == "store":
            out.append(f'<path d="M{x},{LEGEND_Y + 5} v11 a13,4 0 0 0 26,0 v-11" fill="{fill}" '
                       f'stroke="{stroke}" stroke-width="1.75"/>'
                       f'<ellipse cx="{x + 13}" cy="{LEGEND_Y + 5}" rx="13" ry="4" fill="{fill}" '
                       f'stroke="{stroke}" stroke-width="1.75"/>')
        else:
            out.append(f'<rect x="{x}" y="{LEGEND_Y}" width="26" height="18" rx="5" '
                       f'fill="{fill}" stroke="{stroke}" stroke-width="1.75"{dash}/>')
        out.append(f'<text x="{x + 34}" y="{LEGEND_Y + 14}" font-size="13" fill="{TEXT}">'
                   f'{escape(text)}</text>')
        x += 34 + 7.2 * len(text) + 30
    x = LEGEND_X
    for dashed, text in LEGEND_ARROWS:
        dash = ' stroke-dasharray="7 5"' if dashed else ""
        out.append(f'<path d="M{x},{LEGEND_Y + 40} h26" fill="none" stroke="{LINE}" '
                   f'stroke-width="2"{dash} marker-end="url(#arrow)"/>')
        out.append(f'<text x="{x + 34}" y="{LEGEND_Y + 44}" font-size="13" fill="{TEXT}">'
                   f'{escape(text)}</text>')
        x += 34 + 7.2 * len(text) + 30
    # The Pi
    out.append(f'<rect x="{px}" y="{py}" width="{pw}" height="{ph}" rx="20" fill="#fafbfd" '
               f'stroke="#94a3b8" stroke-width="1.5"/>')
    out.append(f'<text x="{px + 24}" y="{py + 32}" font-size="13" font-weight="800" '
               f'letter-spacing="2.5" fill="#475569">{escape(PI_TITLE)}'
               f'<tspan font-weight="400" letter-spacing="0" fill="{MUTED}">   ·   '
               f'{escape(PI_NOTE)}</tspan></text>')
    for c in COLUMNS:
        out.append(f'<text x="{c.x}" y="{py + 72}" font-size="12" font-weight="700" '
                   f'letter-spacing="2" fill="{MUTED}">{escape(c.title)}</text>')
        out.append(f'<path d="M{c.x},{py + 82} h{c.w}" stroke="{HAIRLINE}" stroke-width="1.5"/>')
    # Edges under the boxes, labels over them
    for e in EDGES:
        pts = route(e)
        dash = ' stroke-dasharray="8 6"' if e.dashed else ""
        start = ' marker-start="url(#arrow)"' if e.both else ""
        out.append(f'<path d="{rounded_path(pts)}" fill="none" stroke="{LINE}" '
                   f'stroke-width="1.9" stroke-linecap="round"{dash}{start} '
                   'marker-end="url(#arrow)"/>')
    for n in NODES:
        out.append(_box(n))
    for e in EDGES:
        x, y = label_point(e, route(e))
        out.append(_pill(x, y, e.label))
    out.append("</svg>")
    return "\n".join(out) + "\n"


# -- Excalidraw ----------------------------------------------------------------------


def _id(*parts: str) -> str:
    return hashlib.sha1("/".join(parts).encode()).hexdigest()[:20]


def _seed(*parts: str) -> int:
    return int(hashlib.sha1("/".join(parts).encode()).hexdigest()[:8], 16)


def _base(eid: str, kind: str, x: float, y: float, w: float, h: float, **kw) -> dict:
    el = {
        "id": eid, "type": kind, "x": round(x, 1), "y": round(y, 1),
        "width": round(w, 1), "height": round(h, 1), "angle": 0,
        "strokeColor": "#1e1e1e", "backgroundColor": "transparent", "fillStyle": "solid",
        "strokeWidth": 2, "strokeStyle": "solid", "roughness": 0, "opacity": 100,
        "groupIds": [], "frameId": None, "roundness": None, "seed": _seed(eid),
        "version": 1, "versionNonce": _seed(eid, "nonce"), "isDeleted": False,
        "boundElements": [], "updated": 1, "link": None, "locked": False,
    }
    el.update(kw)
    return el


def _text(eid: str, text: str, x: float, y: float, w: float, h: float, size: int,
          container: str | None, color: str, align: str = "center") -> dict:
    return _base(eid, "text", x, y, w, h, strokeColor=color, text=text, originalText=text,
                 fontSize=size, fontFamily=2, textAlign=align,
                 verticalAlign="middle" if container else "top", containerId=container,
                 lineHeight=1.25, autoResize=True)


def excalidraw() -> dict:
    els: list[dict] = []
    px, py, pw, ph = PI
    els.append(_base(_id("pi"), "rectangle", px, py, pw, ph, strokeColor="#94a3b8",
                     backgroundColor="#fafbfd", roundness={"type": 3}))
    els.append(_text(_id("pi", "title"), f"{PI_TITLE}  ·  {PI_NOTE}", px + 24, py + 16,
                     8 * len(PI_NOTE), 20, 13, None, "#475569", align="left"))
    for c in COLUMNS:
        els.append(_text(_id("column", c.title), c.title, c.x, py + 58, 8 * len(c.title), 18,
                         12, None, MUTED, align="left"))
        els.append(_base(_id("column", c.title, "rule"), "line", c.x, py + 82, c.w, 0,
                         strokeColor=HAIRLINE, strokeWidth=1, points=[[0, 0], [c.w, 0]]))
    els.append(_text(_id("title"), TITLE, 40, 34, 8 * len(TITLE), 34, 28, None, INK,
                     align="left"))
    els.append(_text(_id("subtitle"), "\n".join(SUBTITLE), 40, 74, 8 * len(SUBTITLE[0]), 40,
                     14, None, MUTED, align="left"))
    boxes: dict[str, dict] = {}
    for n in NODES:
        stroke, fill = STYLE[n.kind]
        rid, tid = _id("box", n.id), _id("box", n.id, "text")
        box = _base(rid, "rectangle", n.x, n.y, n.w, n.h, strokeColor=stroke,
                    backgroundColor=fill,
                    strokeStyle="dashed" if n.kind == "optional" else "solid",
                    roundness={"type": 3}, boundElements=[{"type": "text", "id": tid}])
        title = title_of(n) + (f"   ({n.badge})" if n.badge else "")
        text = "\n".join((title, *(ln for ln in n.lines if ln)))
        lines = text.count("\n") + 1
        els += [box, _text(tid, text, n.x + 8, n.y + n.h / 2 - lines * 10, n.w - 16,
                           lines * 20, 14, rid, TEXT)]
        boxes[n.id] = box
    for e in EDGES:
        pts = route(e)
        aid, tid = _id("edge", e.src, e.dst), _id("edge", e.src, e.dst, "text")
        x0, y0 = pts[0]
        rel = [[round(x - x0, 1), round(y - y0, 1)] for x, y in pts]
        xs, ys = [p[0] for p in rel], [p[1] for p in rel]
        arrow = _base(aid, "arrow", x0, y0, max(xs) - min(xs), max(ys) - min(ys),
                      strokeColor=LINE, strokeStyle="dashed" if e.dashed else "solid",
                      points=rel, lastCommittedPoint=None, elbowed=False,
                      roundness={"type": 2},
                      startBinding={"elementId": boxes[e.src]["id"], "focus": 0, "gap": 4},
                      endBinding={"elementId": boxes[e.dst]["id"], "focus": 0, "gap": 4},
                      startArrowhead="arrow" if e.both else None, endArrowhead="arrow",
                      boundElements=[{"type": "text", "id": tid}])
        boxes[e.src]["boundElements"].append({"type": "arrow", "id": aid})
        boxes[e.dst]["boundElements"].append({"type": "arrow", "id": aid})
        lx, ly = label_point(e, pts)
        w = pill_width(e.label)
        els += [arrow, _text(tid, e.label, lx - w / 2, ly - 10, w, 20, 12, aid, LINE)]
    x = LEGEND_X
    for kind, text in LEGEND_BOXES:
        stroke, fill = STYLE[kind]
        els.append(_base(_id("legend", kind), "rectangle", x, LEGEND_Y, 26, 18,
                         strokeColor=stroke, backgroundColor=fill,
                         strokeStyle="dashed" if kind == "optional" else "solid"))
        els.append(_text(_id("legend", kind, "text"), text, x + 34, LEGEND_Y - 1,
                         8 * len(text), 20, 13, None, TEXT, align="left"))
        x += 34 + 7.2 * len(text) + 30
    x = LEGEND_X
    for dashed, text in LEGEND_ARROWS:
        eid = _id("legend", "arrow", text)
        els.append(_base(eid, "arrow", x, LEGEND_Y + 40, 26, 0, strokeColor=LINE,
                         strokeStyle="dashed" if dashed else "solid", points=[[0, 0], [26, 0]],
                         lastCommittedPoint=None, startBinding=None, endBinding=None,
                         startArrowhead=None, endArrowhead="arrow"))
        els.append(_text(_id("legend", "arrow", text, "text"), text, x + 34, LEGEND_Y + 30,
                         8 * len(text), 20, 13, None, TEXT, align="left"))
        x += 34 + 7.2 * len(text) + 30
    return {"type": "excalidraw", "version": 2, "source": "smoking-pi tools/architecture",
            "elements": els,
            "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None}, "files": {}}


def outputs() -> dict[Path, str]:
    return {SVG_OUT: svg(),
            EXCALIDRAW_OUT: json.dumps(excalidraw(), indent=1, sort_keys=True) + "\n"}


def main(argv: list[str]) -> int:
    stale = [p for p, text in outputs().items()
             if not p.is_file() or p.read_text() != text]
    if "--check" in argv:
        for p in stale:
            print(f"stale: {p.relative_to(ROOT)} (run python tools/architecture/architecture.py)")
        return 1 if stale else 0
    for p, text in outputs().items():
        p.write_text(text)
        print(f"wrote {p.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
