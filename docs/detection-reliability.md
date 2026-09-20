# Detection reliability

After several weeks of use the reference installation reported too many false
positives from two detectors: *downtime* (a target, or everything, is down)
and *microcuts* (brief loss on the local link). This page is the record of
both investigations — the real alerts, what the measurements actually showed,
what each should have said, and what changed. The two are independent
efforts with separate evidence and separate backlogs; a fix to one says
nothing about the other.

All evidence is from the reference Pi (Pro edition, InfluxDB, measuring
across a Wi-Fi uplink) between 2026-08-21 and 2026-09-20. Times are UTC.

## Where a detection reaches you

A "detection" arrives by one of two routes, and they have different
definitions:

- **The alerter** (`shared/modules/alerter/`, [alerting.md](alerting.md)):
  rules evaluated every minute, delivered to Telegram through OpenClaw when
  `NOTIFY_MODE` is not `off`, summarized in the daily digest.
- **The assistant** (the OpenClaw skill over the MCP tools,
  [mcp-server.md](mcp-server.md)): `get_loss_events` and
  `get_microcut_stats` are what answer "how is my connection?", and each has
  its own threshold, separate from the alerter's.

On the reference Pi delivery has been `off` throughout, so **every report
the operator read came from the assistant**, whose two tools counted
differently from the rules they mirror. That is the first finding: the
rules had already learned about the CPE loss floor and window jitter; the
tools had not.

---

## Downtime

### Evidence

Loss points in `latency` over 30 days: 94,992 at zero, 3,828 at exactly one
lost ping (10%), 237 at 15–30%, 80 at 35–99%, and 27,260 at 100%. Nearly a
quarter of all points were total loss. Every one of those belonged to one of
three events, and none of the three was a target being down:

| When | What the measurements show | What was reported | What it was |
|---|---|---|---|
| 2026-08-22 → 08-30 (9 days) | 9 hostname targets at 100% every cycle (2,592 points/day); every IP-addressed target (resolvers, OCA caches, gateway) clean | *"several web targets show ~90% loss for the week"* | The SmokePing container's `resolv.conf` froze after a Tailscale logout; names stopped resolving. Fixed in the 2026-08-31 batch (container DNS pin; doctor check `container-dns-fresh`). **The monitor**, not any target. |
| 2026-09-02 22:35 → 09-03 19:35 (21 h) | Every target at 100%, the LAN gateway and the ISP first hop included; no reboot; recovered on its own | (alerter logs from then are gone; by the rules: 18 `target_down` + 18 `high_loss`, re-sent hourly) | Same signature as the next row. **This host's uplink.** |
| 2026-09-20 01:40 → 04:58 (3 h 20 min) | Every target at 100%. `wifi_link`: associated to the AP at −49 dBm, `uplink=1`, ~570 packets *transmitted* per 10 min (the probes going out) and **0 packets received** for the whole span; ended with a reboot | 18 `target_down` criticals + 18 `high_loss` warnings + 1 `microcut_burst`, each re-sent at every cooldown: ~110 notifications for one fact. The verdict line said *"local link cutting out"* | **This host's Wi-Fi radio hung** — brcmfmac with power save on (`iw dev wlan0 get power_save` → `on`), the well-known "connected, receiving nothing" failure. Nothing beyond the radio could be judged. |
| 2026-09-19 00:42 → 00:45 (3 min) | Six consecutive CPE windows at 100% (a real cut); every target got ONE cycle at 60–80% loss; its 15-minute mean cleared 20% | 18 `high_loss` warnings at 00:45–00:46, 18 recoveries at 01:10 | **One brief cut of the link**, reported 36 times. The `microcut_burst` for it was correct. |
| Every day | 60–317 points/day at exactly one lost ping of ten, spread evenly over all 18 targets (Amazon 207, UBA 203 … SpotifyWebPlayer 155 over Sep 5–19); 0–56 points/day at 15–99% | `get_loss_events(min_loss_pct=5)` listed every one; the assistant answered *"packet loss last 6h: yes — 3 target loss events ~9%"* | **The Wi-Fi hop's background.** One lost ping in a 10-ping cycle is not an event. |

What is *not* in the table matters too: in 30 days there was no genuine
single-target outage (the only per-target 100% runs outside those events are
`Google6`, which has no IPv6 route to answer over). The detector's false
positives were all of one kind — **one event with one cause, reported once
per target, plus a background counted as events.**

### Expected classification

- Every target lost every packet for several cycles → one critical,
  scope *this host*: the uplink is down; nothing beyond it can be judged.
  When `wifi_link` shows the radio associated and receiving nothing, say
  that — it is the one failure the router's lights cannot show.
- Every target lossy in the same cycle, then fine → one warning: a brief
  cut of the link, at this time, for this long. No recovery notice — it
  had already ended.
- One target over the mean-loss threshold from a single bad cycle → nothing.
  Loss has to last two cycles to be "high loss".
- One lost ping of ten → not an event; count it as background.

### What changed

**Alerter** (`evaluator.py`, `verdict.py`, `state.py`):

- `rule_widespread` reads the raw down-window points per probe cycle. When
  `WIDESPREAD_PCT` (80%) of the reporting targets are at 100% in each of the
  latest three cycles it emits one `uplink_down` critical; otherwise a run of
  cycles in which that share lost `WIDESPREAD_LOSS_PCT` (30%) becomes one
  `outage` warning keyed by its first cycle. `suppress_widespread` drops
  every `target_down` and `high_loss` while either is active (and
  `microcut_burst` under `uplink_down`, where "99 windows over 50%" says
  nothing the critical did not).
- `high_loss` needs persistence: `HIGH_LOSS_MIN_POINTS` (2) cycles above 15%
  in the window, not one bad cycle averaged over the threshold.
- New verdict scope `monitor_uplink` (🔌), right after `monitoring`. The
  evaluator now also fetches the packets-received increase for the Wi-Fi
  uplink over the down window; zero while associated is named as a hung
  radio, with the fix in the line.
- `outage` is *transient*: the state machine alerts once and drops it
  without a recovery.

**Assistant** (`get_loss_events`, and `common.aggregates.LOSS_EVENT_PCT`
which the digest and the AI report use):

- Default `min_loss_pct` 5 → 15: two or more lost pings, or any lost DNS
  query. The excluded single-ping points are returned as
  `background_points` so the assistant can say they exist.
- `episodes`: consecutive points per target folded into one, with start,
  duration, worst loss and whether it was total.
- `widespread`: runs of cycles in which 80% of the reporting targets had an
  event, with a `cause` line — *this host's uplink* when every target lost
  every packet, *a brief cut of the link* otherwise. Roll-ups are computed
  from up to 5,000 rows, so a three-hour cut across 18 targets (720 points)
  is not misread from the newest 500.

### Replayed against the evidence

Each event above is a test that feeds the detector the rows it saw that night
and asserts what comes out (`alerter/tests/test_evaluator.py`,
`test_verdict.py`, `test_state.py`; `mcp-server/tests/test_tools.py`):

| Event | Before | After |
|---|---|---|
| 2026-09-20 hung radio | 37 incidents, ~110 notifications over the night | 1 `uplink_down`, re-sent at the cooldown: 4 notifications, line *"still associated at −49 dBm but it has received nothing … the radio is hung, not the network"* |
| 2026-09-03 (21 h) | 36 incidents per hour of cooldown | 1 `uplink_down`; without `wifi_link` data the line is the generic *"this host's uplink"* |
| 2026-09-19 blink | 18 warnings + 18 recoveries | 1 `outage` warning, no recovery; `microcut_burst` unchanged |
| a single target's bad cycle | 1 `high_loss` warning + recovery | nothing |
| 143 single-ping points in a day | 143 "loss events" | 0 events, `background_points: 143` |

The 2026-08-22 name-resolution failure is not specifically classified: its
root cause was fixed and the doctor checks for it. If it recurs it would read
as nine `target_down` incidents with the verdict *"9 of 18 affected — not a
clear pattern"*, which is honest if not helpful. Backlog below.

### Downtime backlog

1. **Turn off Wi-Fi power save on the reference Pi.** Not a detector change
   and not done by the PR that added this page — it is a host setting and
   the operator's call: `nmcli connection modify "<SSID>"
   802-11-wireless.powersave 2` (2 = disabled), then reconnect. It is the
   likeliest fix for the two multi-hour hangs.
2. Classify "every hostname target down, every IP target fine" as name
   resolution on the monitor. Needs the alerter to know each target's host,
   which it does not today.
3. A doctor live check for a radio that is associated and receiving nothing,
   so the condition is visible before the alerter has to infer it.

---

## Microcuts

### Evidence

`cpe_latency` windows over 14 days (10 s at 5 pps, one every 30 s; loss is
a percent, so every value is a multiple of 2%):

| Window loss | Windows |
|---|---|
| 0% | 594 |
| 1–10% | 24,260 |
| 11–30% | 12,733 |
| 31–50% | 249 |
| 51–99% | 17 |
| 100% | 417 (all of them the 2026-09-20 hang) |

The gateway's ICMP rate limit is the floor: per day the 90th percentile of
window loss sits at 14–22%, and 98% of all windows show *some* loss. Above
`MICROCUT_LOSS_PCT` (50%), outside the hang, fourteen days held **17
isolated single windows** at 52–78% — thirteen of them on Sep 7–8, the
gateway's worst days (p90 22%, 101 windows at 31–50%) — and **one real cut**:
six consecutive windows at 100% on 2026-09-19 00:42–00:45.

Against that, what the two detectors do:

- `get_microcut_stats` reports `lossy_windows` as *windows with any loss*
  (≈98% of them — meaningless) and always returns a top-5 `worst_windows`,
  so on a quiet day the five worst floor windows (31–50%) come back looking
  like cuts. The assistant duly reported *"strong microcuts last night, worst
  82%"* (Aug 7), *"worst window 20% recently"* (Aug 30) and *"the gateway
  keeps microcutting"* for what was the floor's tail.
- The alerter's `microcut_burst` counts windows above 50% in the last hour
  and fires at `MICROCUT_BURST_N` = 2. On Sep 7 two isolated windows 23 min
  apart (10:17, 10:40) and again 48 min apart (19:02, 19:50) were each a
  "burst"; during the Sep 20 hang it reported *"99 windows over 50%"* beside
  eighteen criticals saying the same thing (now suppressed under
  `uplink_down`, above).

### Expected classification

- A **cut** is a run of consecutive windows above `MICROCUT_LOSS_PCT`, or a
  single window at 100%. Its duration is the run's span.
- An **isolated** window at 51–99% is a *possible* cut: five seconds of
  nothing is real, but one in a day on a rate-limited gateway is not a
  pattern. Report it as such, not as "strong microcuts".
- 31–50% is the floor's tail; ≤ 30% is the floor. Neither is a microcut,
  and a day with many tail windows is "the gateway had a bad day", which is
  worth one sentence with the p90, not a list.
- A burst is several cuts in an hour, not several windows.

### Status

Investigated; fixes not started. Planned as its own change:
`get_microcut_stats` to report `cut_windows` above the threshold instead of
any loss, the day's floor (`p50`/`p90`), cuts folded into runs with
durations, and `worst_windows` only above the threshold (empty, with the
floor stated, when there were none); `microcut_burst` to count runs rather
than windows; the OpenClaw skill's *Reading the numbers* to match.
