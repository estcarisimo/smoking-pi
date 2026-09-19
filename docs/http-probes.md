# HTTP/1.1, HTTP/2, HTTP/3 and TCP probes

ICMP tells you the path is up. It does not tell you whether a page loads, or
whether HTTP/3 is buying anything over HTTP/2 on your link. Four probes
answer that, all of them shipped inside the SmokePing container — no fork
of SmokePing, no fork of the LinuxServer image.

| probe | class | what one sample is | tool |
| --- | --- | --- | --- |
| `CurlHTTP1` | `Curl` | one `GET https://<host>/` over **HTTP/1.1**, DNS excluded | static curl |
| `CurlHTTP2` | `Curl` | the same over **HTTP/2** (TLS + ALPN `h2`) | static curl |
| `CurlHTTP3` | `Curl` | the same over **HTTP/3** (QUIC, `--http3-only`) | static curl |
| `TCPPing` | `TCPPing` | SYN → SYN/ACK to port 443, nothing above the transport | `tcpping` / `tcptraceroute` |

Each runs 5 samples per 5-minute cycle (`pings = 5`, `step = 300`), five
targets in parallel. Nine HTTP targets plus three TCP targets cost about 60
requests per 5 minutes; keep `pings` low, fetching a page 20 times per cycle
is unfriendly to the server.

## What the Curl sample measures

SmokePing's `Curl` probe records `time_total − time_namelookup`: TCP or
QUIC connect, TLS handshake, request, and the response to the last byte
of `/`. DNS is excluded, so a slow resolver does not show up as a slow site
(the DNS probes are for that). Redirects are not followed; a `301` is timed
as a `301`.

The three HTTP probes share one binary, `/usr/local/bin/curl-h3`, a static
[stunnel/static-curl](https://github.com/stunnel/static-curl) build with
ngtcp2/nghttp3 pinned by version and sha256 in
[`shared/modules/smokeping/Dockerfile`](https://github.com/estcarisimo/smoking-pi/blob/main/shared/modules/smokeping/Dockerfile).
Alpine's `curl` (and the official `curlimages/curl`) are built without a
QUIC backend, so HTTP/3 needs its own binary — and once it exists, HTTP/1.1
and HTTP/2 use it too on purpose: one TLS stack for all three, so a
difference between versions is the protocol, not the build.

### The version is enforced, not just requested

`--http2` asks for HTTP/2 and quietly accepts HTTP/1.1 if the server does
not offer `h2`. Charted under "HTTP/2", that sample would be a lie. The
probes close that hole with two `Curl` probe variables and no custom code:

```
extrare  = /;/
extraargs = --http2;-s;-o;/dev/null;-w;Time: %{time_total} DNS time: %{time_namelookup} Redirect time: %{time_redirect} HTTPv=%{http_version}\n
expect   = HTTPv=2
require_zero_status = yes
```

`extraargs` come after the probe's own `-w`, and curl keeps the last `-w`,
so the output line the probe parses is ours; it repeats the fields the
probe's regex needs and appends the negotiated version. `expect` then
requires `HTTPv=2` somewhere in the output, or the sample is dropped. A
downgrade, a failed QUIC handshake, a timeout: all count as loss, which is
what the "Failed fetches" panel shows. `extrare = /;/` splits `extraargs`
on `;` instead of spaces because the `-w` format has spaces in it.

Verified in the container with `smokeping --debug`: every sample line
reads `HTTPv=1.1`, `HTTPv=2` or `HTTPv=3` for its probe.

## How the four probes are configured

In `probes.yaml` (per edition under `config-manager/config/`, defaults in
[`shared/modules/config-manager/templates/probes.yaml`](https://github.com/estcarisimo/smoking-pi/blob/main/shared/modules/config-manager/templates/probes.yaml)):

```yaml
probes:
  CurlHTTP2:
    module: Curl                       # the SmokePing class; name is ours
    binary: /usr/local/bin/curl-h3
    step: 300
    pings: 5
    forks: 5
    timeout: 10
    urlformat: https://%host%/
    require_zero_status: 'yes'
    extrare: '/;/'
    extraargs: '--http2;-s;-o;/dev/null;-w;Time: ... HTTPv=%{http_version}\n'
    expect: HTTPv=2
  TCPPing:
    binary: /usr/bin/tcpping
    step: 300
    pings: 5
    forks: 5
    port: 443
```

`module` is the one key that is ours rather than SmokePing's. Three probes
of the same class become SmokePing *sub-probes*: the generator emits
`+ Curl` once, then `++ CurlHTTP1`, `++ CurlHTTP2`, `++ CurlHTTP3`, each
with its full configuration (SmokePing treats the `+` section as a
template of defaults when sub-sections exist). Targets reference the
sub-probe name: `probe = CurlHTTP3`.

In database mode the same information lives in the `probes` table:
`module` and a JSON `options` column hold what does not fit the native
`binary/step/pings/forks` columns. Both columns are added to an existing
table on startup (`DatabaseManager.create_tables()`), and an
already-migrated deployment picks up the new probes, the new `http` and
`tcp` categories, and those categories' YAML targets on its next start —
categories the database already knows are left alone, since the database
is the source of truth from then on.

## Targets

Three sites that serve all of HTTP/1.1, 2 and 3 ship as examples, plus the
same three over TCP:

```yaml
  http:
    - {name: Google_h1, host: www.google.com, title: Google HTTP/1.1, probe: CurlHTTP1, category: http}
    - {name: Google_h2, host: www.google.com, title: Google HTTP/2,   probe: CurlHTTP2, category: http}
    - {name: Google_h3, host: www.google.com, title: Google HTTP/3,   probe: CurlHTTP3, category: http}
  tcp:
    - {name: Google_tcp443, host: www.google.com, title: Google TCP 443, probe: TCPPing, category: tcp}
```

**Keep the `_h1` / `_h2` / `_h3` suffix.** The exporters read the HTTP
version back from it (`probe_type` = `http1` / `http2` / `http3`; a target
in the `HTTP` section without a suffix is tagged plain `http`), and the
Grafana dashboard groups the three versions of one site by stripping it.
The web-admin add form appends the suffix for you when you pick *HTTPS
fetch* and a version; it also offers *TCP connect (port 443)*.

`host` is a hostname, not a URL: the probe fetches `https://<host>/`. To
probe a path or a different port, change `urlformat` on the probe (it
applies to every target of that probe) or add another sub-probe.

## Where the data lands

| section (RRD dir) | measurement | `probe_type` tag |
| --- | --- | --- |
| `HTTP/` | `http_latency` | `http1`, `http2`, `http3` (or `http`) |
| `TCP/` | `tcp_latency` | `tcpping` |

Same shape as `dns_latency`: `ping1..ping5` and `median` in seconds,
`loss` as a 0–1 ratio (InfluxDB) or `packet_loss` in percent (ClickHouse).
Both exporters classify by the top-level RRD directory, which is the
section name `config_generator.CATEGORY_PRESENTATION` emits — renaming one
means renaming the other.

The dashboard *HTTP by Version – Side-by-Side* (InfluxDB and ClickHouse
variants) shows, per site, the median fetch time of the three versions on
one panel, the failed-fetch share next to it, and the TCP handshake floor
for all targets underneath. The ClickHouse variant was checked on
2026-09-19 against a ClickHouse 24.1 fed by the exporter from the reference
Pi's RRDs: the site variable resolves and all three panels return rows
through Grafana's query API. It has not been looked at in a browser.

## Reading the numbers

- **TCP handshake ≈ one RTT.** Everything above it is protocol cost. On a
  10 ms path, an HTTP/1.1 fetch at 100 ms is 1 RTT of TCP + 2 of TLS 1.2
  (1 of TLS 1.3) + 1 of request/response + server think time.
- **HTTP/3 is not faster on a clean link.** QUIC's wins are loss recovery
  and connection migration; on a wired, lossless path it often lands within
  a few ms of HTTP/2, sometimes behind (UDP in userspace). Where it should
  pull ahead is exactly when the loss panel is non-zero — the point of
  charting the three together.
- **Failed fetches on one version only** is the interesting signal: HTTP/3
  failing while 1.1 and 2 succeed usually means UDP/443 is being dropped
  or rate-limited somewhere on the path (a middlebox, a hotel network, a
  carrier). All three failing is the site or the path.
- **First cycle after a restart** may show HTTP/3 loss on sites that
  advertise `h3` only via `Alt-Svc` — `--http3-only` does not consult it,
  so every target here must accept a cold QUIC connection. The three
  shipped ones do.

## Adding a probe that is not shipped

Three tiers, none of which is a fork:

1. **Shipped class + shipped binary** (`Curl`, `TCPPing`, `DNS`, `SSH`,
   `TraceroutePing`, ...): configuration only, as above.
2. **Shipped class, missing binary** (`openssl` for a raw TLS probe,
   `mtr`): one `apk add` in the smokeping Dockerfile.
3. **A class SmokePing does not have**: a `.pm` file in this repo,
   `COPY`'d into `/usr/share/smokeping/Smokeping/probes/`. SmokePing loads
   any `Smokeping::probes::Foo` by name. A ~40-line subclass of `Curl`
   that swaps the `-w` format to `time_appconnect − time_connect` would
   chart the TLS handshake alone, without the OpenSSL CLI.

The `EchoPing*` probes the template used to list are gone: `echoping` is
unmaintained and not packaged, so they could never have run.
