# Wi-Fi link stats

If the machine running Smoking Pi reaches the internet over Wi-Fi, every
latency and loss figure it records crossed that hop first. A microcut that
coincides with a signal dip or a burst of failed transmissions is a Wi-Fi
problem, not an ISP one — but only if the dip was recorded. The `wifi_link`
collector records it.

It runs inside the `smokeping` container (Pro edition, InfluxDB mode), samples
the wireless uplink every 10 s, and writes one point per sample to the
`wifi_link` measurement. On a host with no wireless interface it logs one
line and idles, rescanning every minute, so a wired Pi pays nothing and a
USB adapter plugged in later is picked up.

## What is recorded

Tags: `interface` always; `ssid` and `bssid` while associated. An
unassociated sample is a series of its own rather than a claim about the
last access point, and a roam shows up as a new `bssid` series.

| field | unit / type | meaning |
| --- | --- | --- |
| `associated`, `uplink` | 0/1 | associated to an AP; this interface carries the default route |
| `signal_dbm`, `signal_avg_dbm` | dBm | received signal (average only on drivers that report it) |
| `noise_dbm`, `snr_db` | dBm / dB | noise floor and signal-to-noise, only when the driver reports a survey |
| `tx_bitrate_mbps`, `rx_bitrate_mbps` | Mbit/s | the negotiated **PHY rate** — see *bitrate vs data rate* |
| `expected_throughput_mbps` | Mbit/s | the driver's own throughput estimate, on drivers that make one |
| `tx_mcs`, `tx_nss`, `tx_width_mhz` (and `rx_*`) | — | modulation, spatial streams, channel width, when the driver spells them out |
| `channel`, `freq_mhz`, `width_mhz`, `band_ghz`, `txpower_dbm` | — | where the link sits |
| `tx_failed`, `tx_retries`, `beacon_loss`, `rx_drop_misc` | cumulative counts | failures; **take `derivative(nonNegative: true)`** for a rate |
| `rx_bytes`, `tx_bytes`, `rx_packets`, `tx_packets`, `tx_dropped` | cumulative | interface counters from sysfs; they survive re-association, unlike the station's own |
| `carrier_down_count` | cumulative | times the link dropped since boot — `increase()` over a range is the disconnect count |
| `connected_seconds` | s | time since the current association |
| `chan_active_ms`, `chan_busy_ms` | cumulative ms | the survey's raw counters; `chan_busy_pct` is their ratio between two reads |
| `chan_busy_pct` | % | share of the channel busy (anyone's traffic), between two survey reads; only with a survey |
| `link_quality` | driver units | the legacy `/proc/net/wireless` quality figure (0–70 on brcmfmac) |

**Bitrate vs data rate.** Two different things, both charted. *Bitrate* is
the PHY rate the two radios agreed on — 433 Mbit/s on a 2×2 80 MHz link — and
is what `iw` prints. *Data rate* is what actually went through, `derivative`
of the byte counters times 8, and is usually a small fraction of the bitrate.
A falling bitrate with a steady data rate is the radio adapting to a worse
channel; a data rate pinned at the bitrate is saturation.

**Not every driver says everything.** The Pi 5's `brcmfmac` reports signal,
bitrates, `tx_failed` and connected time, and no survey — so on the
reference deployment `noise_dbm`, `snr_db`, `chan_busy_pct`, `signal_avg_dbm`,
`tx_retries`, `beacon_loss` and MCS/NSS are simply never written. Panels for
them stay empty and say why. `ath9k`, `iwlwifi` and most USB adapters fill
them in.

## Where the numbers come from

| source | what | why this one |
| --- | --- | --- |
| `iw dev <if> station dump` | signal, bitrates, failures, association | nl80211 is the authoritative interface; reads need no capability, the container just has to share the host's network namespace (it does: `network_mode: host`) |
| `iw dev <if> info` | channel, width, tx power, SSID | changes only on a roam, read every `WIFI_SLOW_INTERVAL` |
| `iw dev <if> survey dump` | noise, busy time | when the driver supports it |
| `/sys/class/net/<if>/…` | byte/packet counters, carrier, `carrier_down_count` | the station's counters reset on every association; these do not |
| `/proc/net/wireless` | `link_quality` | and the whole fallback if `iw` is missing: signal from `level`, association from `carrier`, nothing else |

One `iw` call per fast cycle and two more per slow cycle — eight a minute
at the defaults, on a Pi that once throttled from ~40 process spawns a
minute (`rrd2influx.py`). A new BSSID re-reads the slow set at once, so a
roam does not leave the channel a minute behind the access point.

## Configuration

| variable | default | |
| --- | --- | --- |
| `WIFI_INTERFACE` | auto | the wireless interface to sample; auto = the one carrying the default route (IPv4, else IPv6; lowest metric), else the first wireless one. A non-wireless value disables the collector and logs why |
| `WIFI_SAMPLE_INTERVAL` | `10` | seconds between station samples |
| `WIFI_SLOW_INTERVAL` | `60` | seconds between channel/survey reads |

Set in `editions/pro/.env` (the template lists them); the compose file
passes them to the `smokeping` service. `iw` is baked into the image; the
init script installs it if a custom image lacks it.

Setting `WIFI_INTERFACE` to something that is not wireless — a typo, or a
wired interface asked to report Wi-Fi statistics — disables the collector,
and now says so in the log with the wireless interfaces it did find. It used
to be silent, which read as "no wireless hardware".

### Which interface is the uplink

Every sample carries an `uplink` tag: whether *this* interface is the one the
host's default route sits on. It decides whether the Wi-Fi verdict can fire
at all — *"it's your Wi-Fi, not the ISP"* only makes sense about the link the
measurements cross — so getting it wrong is silent and total.

It is read from `/proc/net/route`, and from `/proc/net/ipv6_route` when there
is no IPv4 default route, so a v6-only host is not mistaken for a host with no
uplink (before that fallback existed, every sample on such a host was tagged
`uplink=0` and the Wi-Fi verdict could never fire). Where both Ethernet and
Wi-Fi are up there are two default routes, and the **lowest metric** wins
rather than whichever the file lists first.

`smoking-pi doctor --live` prints the result as a line of its own —
`measuring over wlan0 (wireless, IPv4)` — and warns when the default route
has moved onto a tunnel or a Docker bridge, which is the case where these
statistics keep being collected while describing a link nobody is asking
about ([Instrumentation doctor](doctor.md)).

## The dashboard

*Wi-Fi Link* (uid `wifi-link-v1`, folder *wifi*, variable `interface`), six
rows top to bottom:

1. **Link now** — SSID, BSSID, channel, width, signal (green above −67 dBm,
   yellow to −75, red below), negotiated TX bitrate, time associated, and
   carrier drops in the selected range.
2. **Signal** — signal and its driver average with the −67/−75 dBm lines
   dashed; noise floor and SNR beside it, empty on drivers without a survey.
3. **Bitrate (PHY rate)** — tx/rx negotiated rate; MCS, spatial streams and
   width where the driver prints them.
4. **Data rate (throughput)** — bit/s and packets/s from the counters.
5. **Quality** — failures, retries, beacon loss and drops per second;
   channel-busy share and link quality.
6. **Roaming and association** — a timeline of the BSSID, a timeline of the
   associated flag, and the cumulative carrier-drop counter.

The **CPE Microcut Detection** dashboard carries one Wi-Fi panel under its
latency panels — signal with TX failures/s on the right axis — so a cut and a
dip share an x-axis. That panel is the reason this collector exists.

Every `links` object the MCP server and the alerter emit can carry
`grafana_wifi_link`; `links.wifi_links(interface, hours, at)` builds a link
into this dashboard zoomed to a moment.

## Saying it: MCP, the verdict, the digest

- **`get_wifi_stats(hours, interface)`** (MCP) answers "how is the Wi-Fi?"
  from the record: the current association (`now`), the window's signal
  min/p10/median/max, the share of samples below `WIFI_WEAK_DBM`,
  disconnects (`increase(carrier_down_count)`), roams (distinct BSSIDs − 1),
  failures, peak throughput, and the five weakest samples each linked to its
  moment. `system_status` carries a `wifi` block so an assistant knows every
  other number crossed that link. Both use `group(columns: ["_field"]) |>
  last()` for "now" — after a roam a plain `last()` would answer from the
  stale series.
- **The verdict** gains a `wifi` scope (📶): when the first hop is cutting
  *and* the uplink had at least `WIFI_WEAK_SAMPLES` samples below
  `WIFI_WEAK_DBM` in the hour, or a carrier drop, the line reads *"Your
  Wi-Fi — the first hop is cutting out and the signal fell to −78 dBm; the
  router or the air, not the ISP."* Otherwise nothing changes except the
  context line, which shows `wi-fi min −54 dBm` so the reader knows the hop
  was in view. A radio that is not the default route is reported, never
  acted on. Details and the precedence table: [alerting.md](alerting.md).
- **The digest** carries a *Local link* line with median/min signal,
  bitrate, disconnects and roams; the AI report's prompt gets the same
  block. Wired hosts see neither.

## Writing queries against it

- Rates from counters: `derivative(unit: 1s, nonNegative: true)`. Totals
  over a range: `increase()`, not `spread()` — a reboot resets the counters
  and `spread` would count the fall.
- "Current state": `group() |> last()`. A plain `last()` returns one row per
  series, and after a roam there are two `bssid` series — the stale one is
  as "last" as the live one.
- In provisioned dashboards, **select fields with `r._field == "…"` only;
  never `pivot` and then write `r.<field>`**. The instrumentation doctor
  treats every `r.<name>` in a dashboard query as a tag reference and fails
  CI for names that no exporter writes as a tag. That is why
  `chan_busy_pct` is computed in the collector rather than as a ratio of
  two fields in Flux.

## Field types are fixed

InfluxDB fixes a field's type the first time it sees it and rejects writes
that disagree. Counters and flags are always written as integers (`associated=1i`,
never a boolean — the client would happily serialize a Python `True` as
`associated=true`, after which every `0` is rejected), levels and rates as
floats. The tests assert the line protocol, not just the values.

## When the radio hangs

Twice in September 2026 the reference Pi lost every target at once for
hours with nothing wrong on the network: 2026-09-02 22:35Z → 09-03 19:35Z
(21 hours, came back on its own) and 2026-09-20 01:40Z → 04:58Z (3 h 20 min,
ended by a reboot). For the second one the `wifi_link` collector — added on
2026-09-19, so it saw only that event — shows the radio still associated and
receiving nothing. The first is classified by its pattern (the same
all-targets shape, no reboot, self-recovered), not by measurement. The
evidence and the detector changes it forced are in
[Detection reliability](detection-reliability.md); this section is about
the radio itself, and about a change that was **documented on purpose and
not made**.

### The signature

- Every target at 100% loss in the same probe cycle — the LAN gateway too.
- `wifi_link` says the link is fine: `associated=1`, a normal signal
  (−49 dBm here), `uplink=1`, `tx_packets` still climbing (the probes go
  out) and **`rx_packets` flat at zero** for the whole span.
- SSH to the Pi over Wi-Fi is dead. `eth0` on the reference Pi has no
  carrier, so there is no other way in.

The verdict names it — *"This host's Wi-Fi (wlan0) — still associated at
−49 dBm but it has received nothing for the whole window: the radio is
hung, not the network. Reconnect the interface or reboot, and turn off
Wi-Fi power save if it recurs."* — as one `uplink_down` incident instead of
one per target. The `rx_packets` comparison behind that line is the
alerter's own; `get_wifi_stats` does not return the raw counter, but its
`throughput_mbps.max_rx` collapses to zero for the window.

### What is known, and what is only suspected

Known: the Pi is a Raspberry Pi 5 on kernel 6.12.25 with the in-tree
`brcmfmac` driver (BCM4345/6, firmware 7.45.265), and **Wi-Fi power save is
on**: `iw dev wlan0 get power_save` answers `on`, and the kernel logs
`brcmf_cfg80211_set_power_mgmt: power save enabled` at every boot.
NetworkManager is not the one turning it on — the connection profile says
`802-11-wireless.powersave: default`, and with no `wifi.powersave` override
in `NetworkManager.conf` on this host that resolves to *ignore*, i.e.
NetworkManager leaves the setting alone. It is on because that is the
driver's own default.

Suspected: that power save is the cause. A station in power save sleeps
between beacons and depends on the AP buffering and announcing its frames;
"associated, transmitting, receiving nothing until reconnect" is the shape
widely reported for this driver family with power save on, and turning it
off is the usual advice. It fits both hangs. It is **not proven** here: two
events, no kernel messages from either (the journal on this host is not
persistent, so a hang's own log lines do not survive the reboot that ends
it), and no A/B run with the setting off.

### Why the change is not simply made

`nmcli connection modify Supersonic 802-11-wireless.powersave 2` is one
line. It is nevertheless the operator's decision, for three reasons:

1. **It is a host setting, reached over the link it changes.** Nothing in
   this repository configures the host's Wi-Fi, and the Pi is headless:
   applying it means dropping and re-raising the connection you are logged
   in over. If the profile comes back wrong, the next step is a keyboard on
   the Pi.
2. **It changes the measurement.** The Pi measures *through* this radio.
   Power save adds wake-up latency to every reply; with it off, the latency
   floor of every target may drop by a few milliseconds and the CPE
   microcut floor may move. That is a change point in a year of data and
   should be dated if it happens.
3. **The evidence is two events.** The detector work made the hang cost
   four notifications instead of a hundred and say what it is; the fix on
   the radio side is a hypothesis. Living with a rare hang that the system
   now reports correctly is a legitimate choice.

### If you decide to try it

Do it in a way that undoes itself:

1. First the non-persistent form, which a reboot reverts:

   ```bash
   sudo iw dev wlan0 set power_save off
   iw dev wlan0 get power_save   # → off
   ```

   This does not drop the link. Leave it for a few weeks; a hang with power
   save off rules the hypothesis out, and none in a period that would have
   held one is (weak) evidence for it.

2. Only then make it persistent, from a session that can afford to lose the
   link — a wired console, or with a scheduled revert armed first:

   ```bash
   sudo systemd-run --on-active=10m --unit=wifi-powersave-revert \
     sh -c 'nmcli connection modify Supersonic 802-11-wireless.powersave 0 && nmcli connection up Supersonic'
   sudo nmcli connection modify Supersonic 802-11-wireless.powersave 2   # 2 = disable
   sudo nmcli connection up Supersonic                                  # re-raises the link
   ```

   If the link comes back, cancel the revert
   (`sudo systemctl stop wifi-powersave-revert.timer`). The reconnect takes
   a few seconds; expect one partial probe cycle and possibly one transient
   `outage` from the alerter, nothing more.

3. **Write the date down** — a CHANGELOG entry, or the project page — so a
   later change in the latency floor can be read.

### While it stays on

A reboot ends a hang (that is what ended the second one); the first cleared
itself after 21 hours. `nmcli device disconnect wlan0 && nmcli device
connect wlan0` from a console would be the gentler first attempt and is
untested here (there is no `nmcli device reconnect`). The doctor does not yet
check for the condition (backlog item 3 under *Downtime* in the detection
page); until it does, the `uplink_down` incident and its verdict line are
the signal.

**Decision record, 2026-09-20:** documented, not applied. Power save stays
on on the reference Pi.

## Not covered

- **ClickHouse.** Like `cpe_latency`, the collector is InfluxDB-only; in
  ClickHouse mode it is not started.
- **Scanning for neighboring networks.** A scan needs `NET_ADMIN` and
  interrupts the link being measured. The channel-busy figure, where the
  driver gives one, is the passive version of the same question.
- **Alerting on Wi-Fi alone.** A dropped uplink takes the whole monitor
  offline, which already shows as exporter-stale and everything-down; the
  useful addition is the verdict *explaining* those in terms of the Wi-Fi
  hop, which the dashboard and MCP tool build toward.
