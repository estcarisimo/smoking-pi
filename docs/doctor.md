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

## What is not covered yet

The live checks — the ones needing a running stack — are not built. They are
the other half of the plan:

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
