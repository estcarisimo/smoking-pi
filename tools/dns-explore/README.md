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
- 🔍 **What coalescing hides**: how many hostnames, CDNs, ASes and organisations sit behind the top-K services.
- 🔁 **Churn**: day-over-day Jaccard of the top-K, per level, score and K.
- 🙈 **The Pi's own traffic left out**: image pulls, tunnels, alert bots, the observer's canary, and names reserved for documentation. The Pi resolves through the router, so this traffic reaches the observer like the house's does.

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
the lookups would land in the very log being analysed.

## 📖 Usage

```bash
# Everything, default K = 5,10,20,50
uv run dns-explore

# Only the last day, services and CDNs by presence, deeper K
uv run dns-explore --window 1d --levels service,cdn --scores presence --k 10,25,100

# Without the Team Cymru lookups (no network at all)
uv run dns-explore --no-asn

# From copies of the log, with extra exclusions, keeping the numbers
uv run dns-explore -f querylog.json.1 -f querylog.json --exclude-file mine.txt --json out/day7.json

# Keep the Pi's own traffic in, to see how much it weighs
uv run dns-explore --no-exclude-own
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
