"""The architecture diagram: one spec, two drawings.

    python tools/architecture/architecture.py          # write both files
    python tools/architecture/architecture.py --check  # fail if they are stale

Writes ``docs/img/architecture.svg`` (shown in docs/architecture.md) and
``docs/architecture.excalidraw`` (open it at excalidraw.com to edit the
drawing by hand). Edit NODES and EDGES here, not the outputs: the tests in
tools/architecture/tests fail when a Compose service, a profile or an
exporter the SmokePing container starts is missing from the spec, or when
the committed drawings differ from what this script writes.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SVG_OUT = ROOT / "docs/img/architecture.svg"
EXCALIDRAW_OUT = ROOT / "docs/architecture.excalidraw"

WIDTH, HEIGHT = 1900, 1010

# kind -> (stroke, fill)
STYLE = {
    "core": ("#1e3a5f", "#dbeafe"),
    "optional": ("#7c2d12", "#ffedd5"),
    "store": ("#14532d", "#dcfce7"),
    "external": ("#475569", "#f1f5f9"),
}


@dataclass(frozen=True)
class Node:
    id: str
    title: str
    lines: tuple[str, ...]
    x: int
    y: int
    w: int
    h: int
    kind: str  # core | optional | store | external
    # Compose services this box stands for, and the profile that turns them
    # on (None: always on). The tests hold both to the Compose files.
    services: tuple[str, ...] = ()
    profile: str | None = None
    # Scripts the SmokePing container starts (custom-cont-init.d).
    exporters: tuple[str, ...] = ()


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    label: str
    via: tuple[tuple[int, int], ...] = ()  # waypoints, to route around boxes
    both: bool = False
    dashed: bool = False
    label_at: tuple[int, int] | None = None  # default: the middle segment's midpoint


NODES: tuple[Node, ...] = (
    # Outside the Pi
    Node("you", "You", ("browser, on the LAN", "or a Cloudflare tunnel"),
         40, 40, 260, 90, "external"),
    Node("targets", "What is measured", ("sites, CDNs, Netflix OCAs,", "public resolvers, the router"),
         470, 40, 360, 90, "external"),
    Node("router", "Home router", ("forwards the house's DNS", "to the Pi (optional)"),
         40, 860, 260, 90, "external"),
    Node("doh", "Encrypted DNS upstreams", ("DoH to 1.1.1.1 / 8.8.8.8", "plain DNS as last resort"),
         1000, 860, 330, 90, "external"),
    Node("chat", "Chat and webhooks", ("Telegram, Slack, webhooks",),
         1600, 330, 280, 80, "external"),
    Node("anthropic", "Anthropic API", ("written health reports",),
         1600, 500, 280, 80, "external"),
    Node("assistants", "AI assistants", ("OpenClaw, any MCP client",),
         1600, 670, 280, 80, "external"),
    # Configure
    Node("web-admin", "web-admin", ("Flask UI :8080", "targets, probes, tour"),
         40, 220, 260, 110, "core", services=("web-admin",)),
    Node("config-manager", "config-manager", ("REST API :5000", "writes SmokePing's config"),
         40, 420, 260, 110, "core", services=("config-manager",)),
    Node("postgres", "PostgreSQL", ("targets, categories,", "probes, sources"),
         40, 620, 260, 100, "store", services=("postgres",)),
    # Measure
    Node("smokeping", "SmokePing", (
        "FPing, FPing6, TCPPing, Curl (HTTP/1.1, /2, /3), DNS",
        "RRD files, one per target",
        "",
        "exporters started in the container:",
        "rrd2influx / rrd2clickhouse: RRD to the TSDB",
        "microcut_detector: sub-step outages",
        "cpe_discovery: the ISP's first hop",
        "wifi_link: Wi-Fi link and uplink",
        "resolver_identity: which resolver answers",
        "dns_wizard: the wizard's snapshot",
    ), 470, 220, 420, 300, "core", services=("smokeping",), exporters=(
        "rrd2influx", "rrd2clickhouse", "microcut_detector", "cpe_discovery",
        "wifi_link", "resolver_identity", "dns_wizard",
    )),
    Node("dns-observer", "DNS observer", ("AdGuard Home :53 + the wizard",
                                          "what the house resolves"),
         470, 700, 420, 100, "optional", services=("dns-observer",), profile="dns"),
    # Store
    Node("influxdb", "InfluxDB", ("time series", "default backend"),
         1000, 250, 230, 390, "store", services=("influxdb",), profile="influxdb"),
    Node("clickhouse", "ClickHouse", ("alternative backend",),
         990, 700, 250, 90, "optional", services=("clickhouse",), profile="clickhouse"),
    # Look and act
    Node("grafana", "Grafana", ("dashboards :3000",),
         1180, 40, 280, 90, "core", services=("grafana",)),
    Node("alerter", "alerter", ("outages, microcuts,", "verdicts, digests"),
         1300, 320, 230, 100, "optional", services=("alerter",), profile="alerts"),
    Node("ai-insights", "ai-insights", ("health reports",),
         1300, 490, 230, 100, "optional", services=("ai-insights",), profile="ai"),
    Node("mcp-server", "mcp-server", ("tools for assistants;", "asks config-manager too"),
         1300, 660, 230, 100, "optional", services=("mcp-server",), profile="mcp"),
    # Host
    Node("host", "On the host", ("smoking-pi CLI, systemd unit,", "Docker Compose: runs all of it"),
         1600, 860, 280, 90, "external"),
)

EDGES: tuple[Edge, ...] = (
    Edge("you", "web-admin", "configure"),
    Edge("you", "grafana", "look", via=((190, 20), (1320, 20))),
    Edge("web-admin", "config-manager", "REST"),
    Edge("config-manager", "postgres", "SQL", both=True),
    Edge("config-manager", "smokeping", "Targets, Probes"),
    Edge("smokeping", "targets", "ICMP, TCP, HTTP, DNS probes"),
    Edge("smokeping", "influxdb", "points"),
    Edge("smokeping", "clickhouse", "rows", dashed=True),
    Edge("grafana", "influxdb", "Flux"),
    Edge("alerter", "influxdb", "reads"),
    Edge("ai-insights", "influxdb", "reads"),
    Edge("mcp-server", "influxdb", "reads"),
    Edge("alerter", "chat", "sends"),
    Edge("ai-insights", "anthropic", "asks"),
    Edge("assistants", "mcp-server", "MCP"),
    Edge("router", "dns-observer", "the house's DNS", via=((170, 780),)),
    Edge("dns-observer", "doh", "upstreams", via=((820, 905),)),
    Edge("dns-observer", "smokeping", "wizard.json"),
    Edge("dns-observer", "config-manager", "wizard.json / Targets", both=True,
         via=((400, 730), (400, 500)), label_at=(400, 665)),
)

LEGEND_X, LEGEND_Y = 380, 865
LEGEND = (
    ("core", "always on"),
    ("store", "data (InfluxDB is the influxdb profile)"),
    ("optional", "a Compose profile turns it on"),
    ("external", "outside the containers"),
)
EDITIONS = "Basic: SmokePing only.  Standard: + web-admin, config-manager, PostgreSQL.  Pro: all of it."


# -- geometry --------------------------------------------------------------------


def node(nid: str) -> Node:
    return next(n for n in NODES if n.id == nid)


def centre(n: Node) -> tuple[float, float]:
    return n.x + n.w / 2, n.y + n.h / 2


def border_point(n: Node, toward: tuple[float, float], gap: float = 4) -> tuple[float, float]:
    """Where the line from the box's centre toward a point leaves the box."""
    cx, cy = centre(n)
    dx, dy = toward[0] - cx, toward[1] - cy
    if dx == 0 and dy == 0:
        return cx, cy
    sx = (n.w / 2 + gap) / abs(dx) if dx else float("inf")
    sy = (n.h / 2 + gap) / abs(dy) if dy else float("inf")
    s = min(sx, sy)
    return cx + dx * s, cy + dy * s


def route(e: Edge) -> list[tuple[float, float]]:
    a, b = node(e.src), node(e.dst)
    first = e.via[0] if e.via else centre(b)
    last = e.via[-1] if e.via else centre(a)
    return [border_point(a, first), *e.via, border_point(b, last)]


def label_point(e: Edge, pts: list[tuple[float, float]]) -> tuple[float, float]:
    if e.label_at:
        return e.label_at
    i = (len(pts) - 1) // 2
    (x1, y1), (x2, y2) = pts[i], pts[i + 1]
    x, y = (x1 + x2) / 2, (y1 + y2) / 2
    if abs(x2 - x1) < 160 and abs(y2 - y1) < 30:  # short and level: above the line
        y -= 14
    return x, y


def title_of(n: Node) -> str:
    return f"{n.title}  [{n.profile}]" if n.profile else n.title


# -- SVG ---------------------------------------------------------------------------


def svg() -> str:
    out = [
        (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
         f'width="{WIDTH}" height="{HEIGHT}" font-family="Helvetica, Arial, sans-serif" '
         'role="img" aria-labelledby="t">'),
        "<title id=\"t\">Smoking Pi architecture: containers, what each reads and writes</title>",
        "<defs>",
        ('<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" '
         'markerHeight="8" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" '
         'fill="#334155"/></marker>'),
        "</defs>",
        f'<rect width="{WIDTH}" height="{HEIGHT}" rx="16" fill="#ffffff"/>',
    ]
    for e in EDGES:
        pts = route(e)
        d = " ".join(f"{'M' if i == 0 else 'L'}{x:.0f},{y:.0f}" for i, (x, y) in enumerate(pts))
        dash = ' stroke-dasharray="8 6"' if e.dashed else ""
        start = ' marker-start="url(#arrow)"' if e.both else ""
        out.append(f'<path d="{d}" fill="none" stroke="#334155" stroke-width="2"{dash}'
                   f'{start} marker-end="url(#arrow)"/>')
    for n in NODES:
        stroke, fill = STYLE[n.kind]
        dash = ' stroke-dasharray="10 6"' if n.kind == "optional" else ""
        out.append(f'<rect x="{n.x}" y="{n.y}" width="{n.w}" height="{n.h}" rx="12" '
                   f'fill="{fill}" stroke="{stroke}" stroke-width="2.5"{dash}/>')
        cx = n.x + n.w / 2
        out.append(f'<text x="{cx:.0f}" y="{n.y + 30}" text-anchor="middle" font-size="20" '
                   f'font-weight="bold" fill="{stroke}">{escape(title_of(n))}</text>')
        for i, line in enumerate(n.lines):
            out.append(f'<text x="{cx:.0f}" y="{n.y + 56 + i * 22}" text-anchor="middle" '
                       f'font-size="16" fill="#1f2937">{escape(line)}</text>')
    for e in EDGES:
        x, y = label_point(e, route(e))
        w = 8.2 * len(e.label) + 12
        out.append(f'<rect x="{x - w / 2:.0f}" y="{y - 12:.0f}" width="{w:.0f}" height="22" '
                   'rx="5" fill="#ffffff" fill-opacity="0.92"/>')
        out.append(f'<text x="{x:.0f}" y="{y + 4:.0f}" text-anchor="middle" font-size="14" '
                   f'font-style="italic" fill="#334155">{escape(e.label)}</text>')
    for i, (kind, text) in enumerate(LEGEND):
        stroke, fill = STYLE[kind]
        y = LEGEND_Y + i * 22
        dash = ' stroke-dasharray="5 3"' if kind == "optional" else ""
        out.append(f'<rect x="{LEGEND_X}" y="{y}" width="26" height="16" rx="3" '
                   f'fill="{fill}" stroke="{stroke}" stroke-width="2"{dash}/>')
        out.append(f'<text x="{LEGEND_X + 34}" y="{y + 13}" font-size="14" '
                   f'fill="#1f2937">{escape(text)}</text>')
    out.append(f'<text x="{WIDTH / 2:.0f}" y="{HEIGHT - 20}" text-anchor="middle" '
               f'font-size="16" fill="#475569">{escape(EDITIONS)}</text>')
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
    boxes: dict[str, dict] = {}
    for n in NODES:
        stroke, fill = STYLE[n.kind]
        rid, tid = _id("box", n.id), _id("box", n.id, "text")
        box = _base(rid, "rectangle", n.x, n.y, n.w, n.h, strokeColor=stroke,
                    backgroundColor=fill,
                    strokeStyle="dashed" if n.kind == "optional" else "solid",
                    roundness={"type": 3}, boundElements=[{"type": "text", "id": tid}])
        text = "\n".join((title_of(n), *n.lines))
        lines = text.count("\n") + 1
        els += [box, _text(tid, text, n.x + 8, n.y + n.h / 2 - lines * 10, n.w - 16,
                           lines * 20, 16, rid, "#1f2937")]
        boxes[n.id] = box
    for e in EDGES:
        pts = route(e)
        aid, tid = _id("edge", e.src, e.dst), _id("edge", e.src, e.dst, "text")
        x0, y0 = pts[0]
        rel = [[round(x - x0, 1), round(y - y0, 1)] for x, y in pts]
        xs, ys = [p[0] for p in rel], [p[1] for p in rel]
        arrow = _base(aid, "arrow", x0, y0, max(xs) - min(xs), max(ys) - min(ys),
                      strokeColor="#334155", strokeStyle="dashed" if e.dashed else "solid",
                      points=rel, lastCommittedPoint=None, elbowed=False,
                      startBinding={"elementId": boxes[e.src]["id"], "focus": 0, "gap": 4},
                      endBinding={"elementId": boxes[e.dst]["id"], "focus": 0, "gap": 4},
                      startArrowhead="arrow" if e.both else None, endArrowhead="arrow",
                      boundElements=[{"type": "text", "id": tid}])
        boxes[e.src]["boundElements"].append({"type": "arrow", "id": aid})
        boxes[e.dst]["boundElements"].append({"type": "arrow", "id": aid})
        lx, ly = label_point(e, pts)
        w = 8.2 * len(e.label)
        els += [arrow, _text(tid, e.label, lx - w / 2, ly - 10, w, 20, 14, aid, "#334155")]
    for i, (kind, text) in enumerate(LEGEND):
        stroke, fill = STYLE[kind]
        y = LEGEND_Y + i * 22
        els.append(_base(_id("legend", kind), "rectangle", LEGEND_X, y, 26, 16,
                         strokeColor=stroke, backgroundColor=fill,
                         strokeStyle="dashed" if kind == "optional" else "solid"))
        els.append(_text(_id("legend", kind, "text"), text, LEGEND_X + 34, y - 2, 8 * len(text),
                         20, 14, None, "#1f2937", align="left"))
    els.append(_text(_id("editions"), EDITIONS, 40, HEIGHT - 40, 8.5 * len(EDITIONS), 20, 16,
                     None, "#475569", align="left"))
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
