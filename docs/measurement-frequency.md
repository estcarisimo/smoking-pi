# Measurement frequency

Every probe measures its targets on a fixed cycle: its **step** (seconds
between measurements) and its **pings** (packets, queries or fetches per
measurement). The shipped probes use a 300 s step. FPing and FPing6 send
10 pings; DNS, HTTP and TCP send 5.

The analysis reads each target's real cycle, so none of it assumes 300 s
and 10 pings. See [Probe cadence](alerting.md#probe-cadence).

## Changing a probe

Open **Probes** in the web admin (Pro). It lists each probe with its cycle
("10 pings every 5 minutes", "5 queries every 5 minutes"), its targets and
an estimate of its traffic. **Change** opens a form:

- **How often:** every minute, 2, 5, 10, 15 or 30 minutes, or every hour.
- **Pings per measurement:** 3 to 20. At least 3, so a loss event (two or
  more lost) is not every single loss.

The form says what the change does and needs a confirmation. It refuses a
cycle that could outrun its step when every ping times out, using
SmokePing's own defaults:

- FPing sends to every target at once, with 1 s between packets to the
  same target, so 20 pings take up to 20 s.
- DNS, TCP and HTTP (Curl) give each ping up to its `timeout` (5 s, or
  10 s for Curl), one after another, and run 5 targets at a time
  (`forks`). Five DNS queries to 12 resolvers can take 3 batches × 5 × 5 s
  = 75 s, which doesn't fit a 60 s step.

The same rules apply to the API:

```bash
curl -X PUT -H "Authorization: Bearer $CONFIG_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"step_seconds": 60, "pings": 10}' \
  http://localhost:5000/probes/FPing
```

The answer says how many targets the probe has and what the RRD guard
moved.

## Why a change needs care: SmokePing's RRD files

SmokePing keeps each target's recent history in an RRD file under `/data`
in the SmokePing container. The file is created for one step and one ping
count, and it cannot change either one later. On every reload SmokePing
checks each file against its probe. **A mismatch is fatal:** the daemon
stops with

```text
Error: RRD parameter mismatch ('Wrong value of step: ... has 300, create string has 60').
You must delete /data/websites/Google.rrd or fix the configuration parameters.
```

and every target stops being measured, not just the one whose file
disagrees. Three things can cause it:

- a probe's step or pings changes;
- a paused target is resumed after its probe changed;
- a deleted target is added again under the same name. On the reference
  Pi, `/data` holds about 180 RRDs for 30 targets, most of them left
  behind by targets deleted long ago.

## The RRD guard

Before every reload, config-manager works out what the new configuration
requires of each RRD. It reads the generated `Targets` and `Probes`,
SmokePing's `Database` defaults, and the CPE targets, which are not in the
database. Then it runs `rrd_guard.py` in the SmokePing container. Every
existing RRD whose step or ping count differs is **moved, not deleted**,
to:

```text
/data/.archive/<UTC time>/<section>/<target>.rrd
```

SmokePing then creates a fresh file and keeps measuring. The config-manager
log says what moved and why:

```text
Archived websites/Google.rrd (step 300, 10 pings) -> .archive/20260924T150000Z/websites/Google.rrd: the probe now wants step 60, 10 pings
```

What this means for history:

- **Grafana keeps everything.** It reads InfluxDB, not the RRDs, and every
  point there records the step and pings it was measured with.
- **SmokePing's own graphs start over** for the archived targets. The old
  file still opens with `rrdtool`:

  ```bash
  sudo docker exec pro-smokeping-1 rrdtool info /data/.archive/<time>/websites/Google.rrd
  ```

- The archive is a dot directory, so neither exporter treats it as a
  target. Nothing prunes it. Remove old stamps by hand when they are no
  longer wanted:

  ```bash
  sudo docker exec pro-smokeping-1 sh -c 'ls /data/.archive'
  ```

A file the guard cannot read is left where it is, and the log names it.
Moving history away on a guess is worse than a reload that fails loudly.
If the guard itself cannot run (an image older than the config-manager),
the reload goes ahead as it always did.

The SmokePing `Database` section also counts its archive sizes (RRAs) in
steps. On a 60 s step, the finest archive (1008 rows) covers about 17
hours instead of 3.5 days. This affects SmokePing's own graphs only.
