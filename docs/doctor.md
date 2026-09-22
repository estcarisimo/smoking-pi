# Instrumentation doctor

Verifies the **instrumentation**, not the network. SmokePing will happily tell
you a target is at 0% loss when nothing is measuring it, and Grafana will
happily render an empty panel that looks like a quiet night. This tool catches
the case where a newly added measurement or panel *looks* fine and silently
charts nothing.

Every check compares what one stage of the pipeline produces against what the
next stage expects:

```
exporter source → dashboard query → datasource → provisioning → Grafana
      config (DB) → generated Targets → SmokePing → RRD → exporter → TSDB → panel
```

Source: `shared/modules/doctor/`.

## Running it

```bash
# From a checkout — auto-detects the repo root
PYTHONPATH=shared/modules/doctor python -m doctor

# Explicit root, machine-readable, or with findings on passing checks too
PYTHONPATH=shared/modules/doctor python -m doctor --repo-root . --json
PYTHONPATH=shared/modules/doctor python -m doctor --verbose
```

Exit code is 1 if any check **fails**; warnings do not fail the build. The
`Grafana dashboards & provisioning` CI job runs the static checks on every PR.

```
instrumentation doctor — static checks against /home/smokingpi/smoking-pi

[ok  ] dashboards-parse              17 dashboards parse
[ok  ] provisioning-yaml-parses      4 provisioning files parse
[ok  ] dashboard-uids-unique         17 uids unique per tree
[ok  ] one-default-datasource        exactly one of 4 datasources is default
[ok  ] datasource-uids-resolve       45 references resolve
[ok  ] datasource-plugins-installed  4 datasources have a usable plugin
[ok  ] dashboards-are-scanned        17 dashboards sit under a scanned path
[ok  ] panel-measurements-written    90 measurement predicates match …
[ok  ] panel-tags-written            98 tag references match …

9 ok, 0 warn, 0 fail, 0 skipped
```

## The static checks

Each one exists because the corresponding failure actually happened here.

| Check | Catches | Real incident |
|---|---|---|
| `dashboards-parse` | Unparseable dashboard JSON | — |
| `provisioning-yaml-parses` | Unparseable provisioning YAML | — |
| `dashboard-uids-unique` | Two dashboards sharing a UID in one tree; a dashboard with no UID at all (provisioning assigns a random one, breaking every deep link on reprovision) | — |
| `one-default-datasource` | Two datasources claiming `isDefault` | **v2.5.0**: ClickHouse was set default, InfluxDB mode provisions both files from a read-only mount, and Grafana refused to start at all |
| `datasource-uids-resolve` | A panel pointing at a datasource uid nothing declares | Eight dashboards referenced `clickhouse-alt`, which never existed |
| `datasource-plugins-installed` | A datasource whose plugin the image does not install — it provisions fine and then cannot answer a query | ClickHouse before the plugin was baked in |
| `dashboards-are-scanned` | Dashboard JSON in a directory no provider walks | The entire ClickHouse dashboard set, on disk and never loaded |
| `panel-measurements-written` | `r._measurement == "X"` where no exporter writes `X` | — |
| `panel-tags-written` | `r.measurement_type` and friends — a filter that can never match | DNS panels filtered on `measurement_type='latency'` / `category='DNS_Resolvers'`, where the exporter writes `dns_latency` / `dns` |

### Where the vocabulary comes from

`panel-measurements-written` and `panel-tags-written` do **not** compare against
a hand-maintained list. They read the exporter source with `ast` and collect
what it actually writes — the string literals passed to `Point(...)` and
`.tag(...)`. A copied list is exactly the kind of thing that drifts silently,
which is the bug class this tool exists for.

`rrd2influx.py` writes its measurement indirectly (`measurement =
measurement_for(...)`, then `Point(measurement)`), so the extractor resolves one
level of indirection through the helper's string-literal returns. Without that
it sees only `cpe_latency` and reports every ping panel as broken — which it
did, on the first run, before the resolution was added.

Panel queries **and** template-variable queries are both checked, including
panels nested inside collapsed rows. The `DNS_Resolvers` mistake lived in a
variable query, not a panel.

## Live checks (`--live`)

```bash
python -m doctor --live
```

Three checks need the running host. Each exists because the failure happened
here, and they share a shape worth naming: **the broken thing keeps looking
healthy**, so nothing goes red and nobody looks.

### `deployed-code-current`

Compares the sha256 of every deployed `.py` — the module's own source and the
shared `common` package — against the repository, for each running container
built from an image that copies source in.

This is commit `dde5e36` ("the flap fix never reached the deployed
container"), and it recurred: an image failed to build, the failure was
masked by a shell pipeline's exit code, `docker compose up -d` recreated the
container from the three-week-old image, and every surface reported success
while the fix sat only on disk. A stale container starts, logs cleanly and
serves requests; there is no symptom to notice.

A container that is *not running* is not drift — a profile switched off is a
deployment choice. A container it cannot read is reported as "cannot verify",
never as drift: claiming a difference it did not measure would be the same
class of bug.

### `container-dns-fresh`

Compares each running container's `/etc/resolv.conf` nameservers against the
host's. Docker writes that file **once, at container creation**, so a
container created while a VPN was up keeps that VPN's resolver forever — and
it dies silently when the VPN goes away.

That cost nine of eighteen targets for ten days: every hostname target read
100% loss, every raw-IP target was fine, and "100% loss" is indistinguishable
from "the target is down". It reports a warning rather than a failure, since
a resolver pinned deliberately via compose `dns:` is legitimate.

Loopback resolvers are ignored. `127.0.0.11` is Docker's own embedded DNS,
present on every container attached to a user-defined network, and it
forwards to whatever the daemon currently resolves with — so it is fresh by
construction. The first real run of this check flagged all six healthy
containers before that exclusion existed, which is how a check gets ignored.

### `uplink-interface`

Names the interface every measurement actually crosses, and says what kind it
is:

```
[ok  ] uplink-interface              measuring over wlan0 (wireless, IPv4)
```

Nothing said this before, and the omission cost a year: the reference Pi has
`eth0` with no carrier and its default route on `wlan0`, so every latency
figure recorded since the stack went up had crossed a Wi-Fi hop nobody was
measuring. Three states are worth a line rather than silence:

- **wireless** — the Wi-Fi collector applies, its dashboards have data, and
  the verdict can say *"it's your Wi-Fi, not the ISP"* ([Wi-Fi uplink
  stats](wifi.md)).
- **wired** — there are no Wi-Fi statistics, deliberately. Without this line
  an empty Wi-Fi dashboard is indistinguishable from a broken collector.
- **virtual** — a warning. The default route is on a Docker bridge, a VPN
  tunnel, Tailscale or WireGuard, so the latency figures describe *that* path,
  and the Wi-Fi verdict is off (it requires the wireless interface to carry
  the default route) with nothing anywhere saying why. This is the failure the
  check is for; the other two are context.

It reads `/proc/net/route`, falling back to `/proc/net/ipv6_route` on a
v6-only host, and compares **metrics** rather than trusting the file's order:
with Ethernet and Wi-Fi both up there are two default routes and only the
lowest metric carries traffic. The kernel does emit them metric-ascending —
verified by adding the high-metric route first in a throwaway namespace and
reading the file back — but nothing documents that, and naming the wrong one
would report the wrong uplink on exactly the host this check exists for. In
`/proc/net/ipv6_route`, `::/0` also appears twice on `lo` as an unreachable
route, so the flags decide, not the destination.

The two Docker checks skip cleanly when Docker is absent, so `--live` is safe
to run anywhere; `uplink-interface` asks the kernel rather than Docker and
answers on any Linux host, skipping only where `/proc/net` is not there.
Without the flag the behavior is exactly as before, and CI is unaffected.

## What is not covered yet

The remaining live checks are not built. They are the other half of the plan:

- **Target added but not measured**: active DB rows vs `++` entries in the
  generated `Targets` vs RRD files on disk vs distinct `target` tags in the
  TSDB. Any target present at one stage and missing at the next is a finding.
- **Queries that error or always return empty**: execute each panel query over
  a recent window and distinguish "query error", "ran but zero rows", and "ok".
- **Dashboards not provisioned**: dashboard JSON on disk vs what Grafana's API
  reports.
- **Tag *values* nothing writes**: the static checks verify measurement and tag
  *names* against the exporter source; whether `category == "topsites"` ever
  actually occurs can only be answered by the TSDB.
- **Measurement written but never displayed**: the reverse direction — a new
  metric someone added and forgot to chart.
- **Unit/semantics drift**: loss outside its declared range per measurement
  (ratio 0–1 for `latency`/`dns_latency`, percent 0–100 for `cpe_latency`),
  which is how both loss-denominator bugs would have announced themselves.

These need the InfluxDB token, the config-manager API, the Grafana API, and the
RRD directory — which lives in the `smokeping-data` named volume, so they will
run from a container under a `doctor` compose profile rather than from the host.

Exposing the doctor as an MCP tool, so it can be asked for from chat, comes with
that work.
