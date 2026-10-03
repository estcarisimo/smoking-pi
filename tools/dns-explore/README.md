# 🧭 dns-explore

Reads what the DNS observer saw and asks how diverse, how concentrated and
how stable the house's DNS activity is. It measures this at several
aggregation levels, scores and depths (top-K), so that the design of
automatic target selection can pick its levels and thresholds from
measurements instead of guesses. It changes nothing: no targets, no
settings.

This is iteration 0 of the domain-selection design, "Selección de dominios:
de observaciones DNS a targets" in the project's Notion.

## ✨ Features

- 🧱 **Five aggregation levels**:
  - `fqdn`: the name as asked;
  - `service`: eTLD+1, by the Public Suffix List;
  - `cdn`: the registrable domain at the end of the CNAME chain;
  - `asn`: the origin AS of the answer's address;
  - `org`: the AS's registered name.
- 📏 **Three scores**:
  - `queries`: every query;
  - `uncached`: queries AdGuard sent upstream;
  - `presence`: distinct hours seen, which resists telemetry bursts and caching.
- 📊 **Diversity and concentration**:
  - richness;
  - HHI (Herfindahl–Hirschman) and its effective number, 1/HHI;
  - Shannon entropy and its effective number, exp(H);
  - coverage@K.
- 🔍 **What coalescing hides**: how many hostnames, CDNs, ASes and organizations sit behind the top-K services.
- 🔁 **Churn**: day-over-day Jaccard of the top-K, per level, score and K.
- ⚖️ **Stability and enter/leave rules**: per level, score and K, how many units were ever in a daily top-K, in it every day or on one day only; how often one came back and after how long an absence (an exit rule must wait longer); how many units tie at the K-th score (presence saturates at 24 hours a day, so a tie means the score ranks nothing there, and the tie-break by name makes such a row look stable when it is not); and what each rule in `--rules E:L` would have done: added after E days in the daily top-K, removed after L days out. `1:1` replays the raw churn.
- 📅 **Whole days only** for churn and stability: the day a log starts in and the day it ends in rank fewer hours and look like churn, so they are left out and named (`--all-days` keeps them). A day counts as whole when the log has it within an hour of each midnight. A day missing from the middle of the log is unknown, not adjacent: it breaks every enter/leave streak, lengthens an absence already seen, and never starts one.
- 🙈 **The Pi's own traffic left out**: image pulls, tunnels, alert bots, the observer's canary, and names reserved for documentation. The Pi resolves through the router, so this traffic reaches the observer like the house's does.
- 📡 **SmokePing's lookups left out too**: the names it measures, read from the install's generated `Targets` (`--targets`). It looks each one up about once per TTL, all day and all night; on the reference Pi that was a third of the log.

## 🚀 Quick Start

On the Pi that runs the DNS observer (Pro edition, `dns` profile):

```bash
cd tools/dns-explore
uv sync
uv run dns-explore
```

It reads the log from the `pro-dns-observer-1` container with `docker exec`,
so it needs no admin password. Origin ASes come from Team Cymru's DNS
service, asked of `1.1.1.1` directly and not through the router: otherwise
the lookups would land in the very log being analyzed.

## 📖 Usage

```bash
# Everything, default K = 5,10,20,50
uv run dns-explore

# Only the last day, services and CDNs by presence, deeper K
uv run dns-explore --window 1d --levels service,cdn --scores presence --k 10,25,100

# Replay other enter/leave rules (E days in to add, L days out to remove)
uv run dns-explore --window 7d --levels service,asn --rules 2:3,3:7,5:7

# Without the Team Cymru lookups (no network at all)
uv run dns-explore --no-asn

# From copies of the log, with extra exclusions, keeping the numbers
uv run dns-explore -f querylog.json.1 -f querylog.json --exclude-file mine.txt --json out/day7.json

# Keep the Pi's own traffic in, to see how much it weighs
uv run dns-explore --no-exclude-own

# SmokePing's lookups of what it measures are left out by default (found in
# the install; they are a third of the log once the wizard has adopted).
# Point at another Targets file, or keep them in:
uv run dns-explore --targets /var/lib/smoking-pi/output/Targets
uv run dns-explore --targets none
```

Churn needs at least two calendar days of log. AdGuard keeps 7 days
(`DNS_RETENTION_DAYS`). Entries still in its memory buffer (the last 1,000
queries) are not on disk yet and are missed.

## 🧪 Development

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

The tests use synthetic log lines and touch neither the network nor Docker.

## 📄 License

Same as the repository: see [LICENSE](../../LICENSE).
