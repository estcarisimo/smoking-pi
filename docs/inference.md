# Congestion and degradation detectors (experimental)

!!! warning "Experimental"
    These detectors are off by default, are not in the alert path, and may
    change or go away without a deprecation period. Their results shade the
    Target Detail dashboard; nothing pages anyone. Validate them against
    your own connection before you rely on them.

The `inference` service runs two detectors on every ICMP target, once an
hour. They answer different questions and use different signals, so they
are kept apart: one never feeds the other.

| | Persistent congestion | Loss degradation |
|---|---|---|
| Question | Did a queue on the path stay built up? | Did the target start losing packets, and keep losing them? |
| Signal | The minimum RTT, then the RTT distribution | Packet loss |
| Method | [Jitterbug](https://github.com/estcarisimo/jitterbug), as published | Bayesian change points on loss |
| Window | the last 14 days | the last 7 days |
| InfluxDB measurement | `persistent_congestion` | `loss_degradation` |
| Shade on Target Detail | orange | red |

## Persistent congestion: Jitterbug

[Jitterbug](https://github.com/estcarisimo/jitterbug) (Carisimo et al.,
*Jitterbug: A New Framework for Jitter-Based Congestion Inference*,
PAM 2022) is used as its package publishes it, `jitterbug-inference` with
the `bcp` extra, in the paper's configuration: Bayesian change-point
detection (BCP) and the Kolmogorov-Smirnov jitter test. Every other option
stays at its default.

1. The RTTs are binned into 15-minute minimums, which removes most of the
   queueing noise.
2. BCP finds the change points of that minimum.
3. A period is a candidate when its minimum rose over the previous one's by
   more than 0.5 ms (the latency jump).
4. The KS test compares the RTT distributions on either side.
5. A period is congested when both say so.

What it finds is a queue that stays built up: the floor of the RTT rises and
stays risen. A path whose minimum never moves gets no congestion period,
however jittery or lossy it is; that is a correct answer, not a miss. Each
period carries Jitterbug's confidence and the size of the latency jump.

SmokePing stores the pings of each cycle sorted, so the order in which they
were sent is lost. The KS test compares distributions and does not depend on
that order, which is one more reason the paper's configuration is the one
used here.

## Loss degradation: change points on packet loss

Loss can come from the radio, a faulty line, an overloaded router or a
policer, not only from a queue that stays full, so it is a separate signal
with its own detector rather than evidence for Jitterbug.

1. Each probe cycle's loss is averaged into 15-minute bins, the bin
   Jitterbug uses for the minimum RTT.
2. Bayesian change-point detection runs on that series exactly as Jitterbug
   runs it on the minimum (a constant prior, a Student-t likelihood, a change
   point where the probability exceeds 0.25).
3. The bins between two change points are a segment. A segment is degraded
   when it lasts at least 30 minutes and its mean loss is at least 2 %.
   Adjacent degraded segments are reported as one.

With 10 pings a cycle and 3 cycles a bin, a single lost ping reads 3.3 % in
its bin. Change-point detection may well cut a segment around it; the
30-minute floor is what keeps one lost ping from being a degradation.

Reading the shading across targets says where the loss is. The same period
on every destination and on the CPE target is the local link or the Pi;
on every destination but not on the CPE, it is beyond the first hop; on one
destination only, it is that destination.

## What a period carries besides its detection

The detectors run on the raw series, as published. Two things are read off
other evidence and written beside each period, never fed back into it:

- **`class`: `loss` or `monitor_deaf`** (degradation only). A radio hang
  records 100 % loss on every destination for hours, and BCP on loss then
  finds a "degradation" of every target that is really the monitor being
  deaf — 22 of the first 51 periods on the reference Pi were this. The
  period is labeled `monitor_deaf` when at least half of its 15-minute bins
  were deaf by the alerter's own test (`common/microcuts.py`: the uplink
  interface was not associated, or its received-packet counter did not
  move), with the share in `deaf_share`. Target Detail shades those gray
  under *Monitor deaf (experimental)* and says so in the text; Connectivity
  & Quality draws them gray on the timeline and leaves them out of the
  degradation count. A wired host never has a deaf period.
- **`coverage`**: the share of the period's bins that had data — for a
  congestion period, bins with at least one answered ping (what Jitterbug
  reads; a bin of total loss is empty to it), for a degradation period,
  bins with at least one probe cycle (loss is recorded even when nothing
  answers). A congestion period that spans a gap of hours (a hang, a
  reboot) reads as such — "data in 40 % of the span" in its annotation —
  instead of looking like a solid span; the first read of the reference Pi
  found a 49-hour period of a 3.7 ms jump that straddled two hangs.
  The deaf samples come from the same query the alerter uses, capped at
  20 000 rows (about 55 hours of deaf time at the default 10 s cadence);
  past that the service logs a warning and the newest deaf periods may read
  as loss.

## Pass history

Every pass writes one `inference_pass` point: `seconds`, `targets`,
`errors`, `congestion_periods`, `degradation_periods`. The
[Diagnostics dashboard](diagnostics.md) (Advanced folder) charts the seconds
and failures per pass; `status.json` keeps only the last pass, and the
container's log does not survive an upgrade, so this is the record of what a
pass costs over time.

## Turning it on

Pro edition, InfluxDB backend:

```bash
sudo smoking-pi enable inference
```

(the other services stay as they are). The first pass starts two
minutes after the container. Then open **Target Detail** for a target: the
two annotations, *Persistent congestion (Jitterbug, experimental)* and
*Loss degradation (experimental)*, shade the periods found, and hovering a
region shows its numbers. They can be switched off from the dashboard's
annotation toggles. The [Connectivity & Quality](connectivity-quality.md)
dashboard draws every target's periods on one timeline, under the loss and
latency they are about.

`docker exec <project>-inference-1 python status.py` prints the last pass:
per target, the change points and periods found, and how long it took.

## Settings

All optional; empty means the default.

| Variable | Default | Meaning |
|---|---|---|
| `INFERENCE_INTERVAL` | `3600` | Seconds between passes |
| `INFERENCE_CONGESTION_DAYS` | `14` | Jitterbug's window, close to the 16 days of the paper's dataset |
| `INFERENCE_DEGRADATION_DAYS` | `7` | The loss window |
| `INFERENCE_SINCE` | — | `YYYY-MM-DD`: never look before this date. Set it after moving the Pi or changing provider: the old connection's history is not this one's baseline |
| `INFERENCE_CATEGORIES` | `topsites,custom,cpe` | Which ICMP targets, by InfluxDB category tag. Netflix OCAs (the appliance behind each one changes at the nightly refresh, which would read as a change point; `target_host` records when) and the DNS wizard's targets are left out by default |
| `INFERENCE_TARGETS` | — | Exactly these targets instead (comma-separated) |
| `INFERENCE_DEGRADATION_MIN_MINUTES` | `30` | Shortest degradation |
| `INFERENCE_DEGRADATION_LOSS_PCT` | `2` | Lowest mean loss of a degradation |

## Cost

Change-point detection grows with the square of the number of bins, which is
why the windows are bounded. On a laptop, one target costs about 1.5 s for
Jitterbug over 14 days and under 1 s for the loss over 7 days; expect several
times that on a Raspberry Pi, every hour, for each selected target. The
service runs at a lower CPU priority and is limited to one core, so the
measurements always come first. It only reads and writes InfluxDB; it sends
nothing to the network.

Each pass replaces the results inside its window: a period found again is
rewritten, not duplicated, and one no longer found disappears. A period cut
by the start of the window is left as an earlier pass recorded it. If a
pass fails after clearing its window, the window shows nothing until the
next pass. `status.py` reports the time of the last pass without errors
(`last_ok`).

## Limits

- **InfluxDB only.** With `TSDB_TYPE=clickhouse` the service stays idle and
  says so in its status.
- **ICMP only.** DNS response times are a different signal: a resolver under
  load answers more slowly without any queue on the path, and on a Pi the
  DNS probe's timer resolution used to be the kernel tick (4 ms on a
  250 Hz kernel), which left the minimum on a few fixed steps that
  Jitterbug's minimum cannot move between. The probe now reads dig at
  microsecond resolution (`dig-usec`, see [DNS probes](dns-probes.md)), but
  the history before that change is still quantized, so a window that
  spans it mixes the two.
- **Not in the alert path.** Alerts and other ways of passing these results
  on come later, once each detector has been validated on real connections.
- The image is larger than the others: the detectors need PyTorch (the
  CPU-only build).
