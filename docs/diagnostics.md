# Diagnostics dashboard

The **Diagnostics** dashboard (Grafana folder *Advanced*) is for the person
who maintains a Smoking Pi, not for the question "is my connection fine".
It answers the questions that come up when another dashboard looks wrong:
is every collector still writing, which target series stopped, did the copy
in InfluxDB have a gap, and what do the services record about their own
runs. It is the InfluxDB tree's only dashboard about the monitor itself; the
ClickHouse backend has no version of it.

Open it from the Overview's *Where to look next* list or from the
*SmokePing dashboards* dropdown. Its default range is the last 24 hours.

## What it shows

| Row | Panel | What it answers |
| --- | --- | --- |
| Collectors | SmokePing probes, by layer | How old the newest ICMP, DNS, TCP, HTTP and ISP-gateway point is. Orange past two steps and a minute (the rule the web admin applies per target), red past half an hour |
| | Host collectors | The same for the Wi-Fi sampler, the uplink byte meter, the public-address and resolver-identity collectors and the uplink heartbeat, each against its own cadence |
| | Services | The same for the inference module's passes, the budget report, the DNS wizard's hourly snapshot and the house's DNS timing |
| Series that stopped | Silent targets | Every target that wrote in the last 24 hours and has been silent longer than its window, longest silence first. Click a target for every layer it is measured on |
| | Silent targets (count), Series with data (24 h) | The table's size and the population it is drawn from. `0` silent is the normal reading; a target you just removed, paused or renamed reads silent for up to 24 h, until its last point ages out of the window |
| | Points written per 15 minutes, by layer | A flat line per layer is normal. A dip every layer shares is a gap in the InfluxDB copy (the database down, the card full, the exporter restarting); a dip in one layer is that probe |
| The services, about themselves | Inference passes | Seconds and failures per hourly pass, moved here from Connectivity & Quality (it is the module's health, not the connection's) |
| | What the watchdog did | Driver reloads, reboots and SmokePing restarts the host watchdog recorded (`watchdog_event`). Empty is the healthy reading |
| | Loss incidents the alerter diagnosed | One row per closed incident: class, summary, minutes, confidence (low, medium or high), targets affected |
| | Measurement budget report | Whether the budget exporter priced every probe, how many it could not, and whether the estimate is over the ceiling. These flags were written since v2.16.0 and shown nowhere until now |
| Text | Empty on purpose; where the rest lives | Why a panel is blank on this hardware, and which command or card holds the health that never reaches InfluxDB |

## What it cannot show

The dashboard reads InfluxDB, so it only knows about collectors that write
there. A target that was configured but **never** measured has no series to
be silent: that is the web admin's *Measurements* card and the doctor's
`targets-measured` and `silent-series` checks, which compare against the
configured targets. The DNS observer, the mDNS responder and the alerter keep
no heartbeat in the database; their state is in `smoking-pi dns status`, the
container healthchecks (`smoking-pi status`) and the web admin's cards. The
alerter's dead-man check-in lives off the host on purpose
([alerting](alerting.md)).

The watchdog panel is empty until the watchdog has had to act. Events are
recorded in InfluxDB since v2.32.0; `smoking-pi watchdog status` shows the
full log, including the enable and disable marks, which are not written to
the database.

## Freshness rule

"Silent" means the last point is older than **two steps plus a minute**,
the same rule config-manager's freshness module applies to the RRD files
for the web admin's card (one missed step is a slow probe round, two is a
problem). The step comes from the `step` field the exporter writes with
every point; the ISP gateway's high-frequency probe writes about once a
minute and has its own thresholds. The per-target query runs over the last
24 hours and took 0.4 s on the reference Pi with 247 targets.

## See also

- [Instrumentation doctor](doctor.md): the checks that compare the pieces
  against each other, static and live.
- [Watchdog](watchdog.md): what the host watchdog does and records.
- [Measurement budget](measurement-budget.md): what the budget report's
  flags mean.
- [Congestion and degradation](inference.md): the inference module whose
  passes this dashboard charts.
