---
name: smokeping-monitoring
description: Answer any question about the home internet connection, network health, latency, packet loss, outages, microcuts, or "how is my connection / cómo está mi conexión / qué onda la red" using the smokeping MCP tools, which hold this Pi's continuous measurement history. ALSO use when the user names the monitoring host or project (smokingpi, smoking-pi, smokeping, "the Pi") and asks how things are. NOT for: running live ping/curl/speed tests in a shell — the measurement already exists and covers the past, which a live probe cannot.
---

# SmokePing Monitoring

You have tools from the `smokeping` MCP server covering a Raspberry Pi that
has been continuously measuring this home network. Use them to answer
questions about latency, loss and outages, and to manage what is monitored.

Everything below the line is the same guide every assistant connected to
Smoking Pi receives as the server's instructions; this header adds only what
is particular to OpenClaw and Telegram.

**Tune the `description:` above before installing.** OpenClaw uses it to
decide whether to load this skill at all, so it must contain the words *you*
use for the machine ("the router box", "casa") and the language you ask in —
trigger phrases are matched, not translated. Change it in
`openclaw-header.md` and rebuild (`shared/scripts/build-openclaw-skill.py`).

## Telegram: this channel is HTML

Messages are sent with `parse_mode: "HTML"`, so a section heading is
`<b>DNS</b>` — the only bold that renders here. `### DNS` arrives as literal
hashes and `**DNS**` as literal asterisks. Escape `<` and `&` in values
(`&lt;`, `&amp;`): target names are user-editable. Never wrap a whole
message in a code block; it kills every heading in it.

```
Stable week, two short cuts on the line Friday night. 🟡

<b>Internet</b>
🟢 Medians 7–9 ms on the big sites.

<b>Local link</b>
🟡 Two cuts, 40 s and 2 min — Fri 28 Aug, 2:30 am CT. [graph]
🟢 Wi-Fi −52 dBm median, no disconnects. [wi-fi]

<b>Bottom line:</b> nothing to do; a call at 2:30 am would have dropped.

<b>Graphs</b>
• Overview — home: <url> · anywhere: <url>
```

`get_chart(deliver=true)` also posts the PNG into this chat as a file, so
it can be forwarded; use it when the person wants to send the picture on.
