# The Pi's name on the network

Every edition answers for **`smoking-pi.local`** on the local network, so
the Pi can be opened at `http://smoking-pi.local:8080/` (Basic: on
`SMOKEPING_PORT`, `http://smoking-pi.local/` by default) without knowing
its address. The name does not
depend on the Pi's hostname, survives a DHCP lease changing the address,
and works on hosts that do not run Avahi.

It is the `mdns` service: a small multicast DNS responder (RFC 6762) on
the host network, in its own container. It claims one name, answers for
it with the Pi's LAN addresses, and nothing else. The DNS-SD announcement
that `smoking-pi discover` lists is a separate thing, written for the
host's Avahi (see [Getting started](getting-started.md)).

## Why not the hostname

Raspberry Pi OS already answers for `<hostname>.local` through Avahi. On
the reference Pi that name broke twice: eight seconds after a boot Avahi
reported `Host name conflict, retrying with smokingpi-2` and from then on
answered only for `smokingpi-2.local`. Nothing else on the network had the
name; Avahi heard its own announcement come back (a Wi-Fi access point
that echoes multicast does this) and took it for another host. The fix
was a manual `systemctl restart avahi-daemon`, until the next boot.

The `mdns` service avoids both halves of that:

- packets from the Pi's own addresses are never a conflict, and neither
  are records identical to its own;
- the name it holds is written to its status, so `smoking-pi url` prints
  the name that answers instead of guessing `$(hostname).local`.

## Two Smoking Pis on one network

The name is probed before it is used. If another host already answers for
`smoking-pi.local` with other addresses, the second Pi takes
`smoking-pi-2.local` (then `-3`, and so on) and logs it:

```text
WARNING smoking-pi.local is taken by another host; trying smoking-pi-2.local
```

Which Pi gets the plain name then depends on which started first, and a
Pi on `-2` keeps it while it runs (it does not take the plain name back
when the other leaves; a restart probes from the plain name again). After
twenty taken names it stops, says so in its log and status, and starts
over from the plain name five minutes later. Give each Pi its own name
instead:

```bash
sudo smoking-pi config set MDNS_NAME smoking-pi-lab
```

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `MDNS_NAME` | `smoking-pi` | The name to claim, without `.local`: letters, digits and inner hyphens. `off` answers for no name. |
| `MDNS_INTERFACES` | every LAN interface | Comma-separated interfaces to answer on (`wlan0,eth0`). Docker bridges, veths, VPNs (Tailscale, WireGuard) and the loopback are never used unless listed here. |

`config set` applies the change (the container is recreated). The answer
carries the IPv4 address and every stable global and unique-local IPv6
address of those interfaces; link-local and temporary ones are left out.
A Pi without IPv6 says so (an NSEC record), so a client asking for both
does not wait for an AAAA that will never come. The responder speaks
multicast DNS over IPv4 only; that is where every common resolver asks.

Do not set `MDNS_NAME` to the Pi's own hostname: Avahi already answers
for that name, with a different set of addresses, and the two would
contend for it.

The name is for **other machines** on the network. The responder ignores
packets from the Pi's own addresses (that is what keeps it from fighting
itself), so on the Pi use `localhost` or the address.

## Checking it

On the Pi:

```bash
sudo smoking-pi url
sudo smoking-pi logs mdns
```

`url` ends with the name held, for example
`Also, from most computers on this network: http://smoking-pi.local:8080/`.
The log says what it claimed and on which interfaces:

```text
INFO listening on wlan0 (192.168.1.10)
INFO probing for smoking-pi.local
INFO answering for smoking-pi.local at 192.168.1.10, fd00::10
```

From another Linux machine on the network, either of these resolves it:

```bash
getent hosts smoking-pi.local
dig @224.0.0.251 -p 5353 smoking-pi.local +short
```

On a Mac, `dns-sd -G v4 smoking-pi.local`. Windows 10 and later resolve
`.local` names in the browser.

## When the name does not resolve

- **A guest Wi-Fi or client isolation** blocks multicast between devices:
  nothing on that network can resolve any `.local` name. Use the address.
- **The client has no mDNS resolver.** Most desktops do. A minimal Linux
  without `nss-mdns` (or systemd-resolved with MulticastDNS on) does not;
  `dig @224.0.0.251 -p 5353` above still works there.
- **The container is not running or still probing**: `smoking-pi status`,
  then `smoking-pi logs mdns`. Probing takes under two seconds.
- **The name is `smoking-pi-2.local`**: another host holds the plain name;
  see [Two Smoking Pis on one network](#two-smoking-pis-on-one-network).
- **The client is on another network** (a VPN, a Docker bridge, a
  routed subnet): multicast DNS stays on the link, and the responder only
  answers packets that arrived on a LAN interface with IP TTL 255.
- **A firewall on the Pi** must let UDP 5353 in on the LAN interface. The
  host's Avahi needs the same, so a Pi where `<hostname>.local` resolves
  already allows it.

The name is readable by everyone on the network, like the Pi's hostname;
it carries no version, port or edition.
