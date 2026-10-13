# Watchdog

A monitor that stops measuring by itself, and stays stopped until someone
power-cycles it, loses exactly the hours you wanted to see. Two such failures
have happened on Smoking Pi hosts, more than once each, and before this
command nothing recovered from either without a person:

- **The Wi-Fi radio hangs.** `wlan0` stays up, often still associated to the
  access point, and receives nothing. Every target goes to 100% loss, the LAN
  router included, and SSH over Wi-Fi is dead. The hangs lasted 21 hours,
  3 h 20 min and 17 hours, and a power cycle ended most of them. The
  signature, and what is known and suspected about the cause, are in
  [Wi-Fi uplink stats](wifi.md#when-the-radio-hangs).
- **SmokePing does not start.** After a power cycle the SmokePing container
  was running, but the SmokePing service inside it stayed
  `down (not started yet)`: its startup sequence stalled. Nothing was
  measured for 16 minutes, until a manual `smoking-pi restart smokeping`. A
  reboot of another Pi on the same release came up normally: it is a race at
  boot, not a bug that repeats.

`smoking-pi watchdog` checks for both every minute and puts them right.

## Turning it on

```bash
sudo smoking-pi watchdog enable
```

This installs two systemd units, `smoking-pi-watchdog.timer` and
`smoking-pi-watchdog.service`, in `/etc/systemd/system`. The timer starts
two minutes after boot and then runs `smoking-pi watchdog check` every
minute, as root. `sudo smoking-pi watchdog disable` removes both units.
Uninstalling the package removes them too.

To have the watchdog reload the driver but never reboot the host, use:

```bash
sudo smoking-pi watchdog enable --no-reboot
```

The watchdog is off until you turn it on: it can reboot the host, and that
is your decision to make.

## What it does

### The Wi-Fi radio

Each check pings the router (the default route's gateway) through the Wi-Fi
interface, and, if it stays quiet, two public addresses (`1.1.1.1`,
`8.8.8.8`) through the same interface. One answer from any of them is
enough. If none answers, the check looks at whether the router still
answers on the link: the pings made the kernel ask for the router's address
(ARP). A working radio hears the reply; a deaf one does not, so its
neighbor entry for the router never becomes `REACHABLE` (`ip neigh`). The
watchdog only acts when **nothing** answers, at either level:

- An ISP outage leaves the router answering ping, so the watchdog does
  nothing.
- A router that ignores ping still answers on the link, and the Internet
  answers through it when it is up. Either one keeps the watchdog quiet.

| Nothing answers through Wi-Fi for | Action |
| --- | --- |
| 10 minutes | Reload the Wi-Fi driver: remove its kernel module and the modules that hold it (on a Raspberry Pi, `brcmfmac` and `brcmfmac_cyw` or `brcmfmac_wcc`), then load them again. NetworkManager reconnects by itself. |
| 5 more minutes | Reboot the host, unless the watchdog already rebooted it in the last 6 hours or was enabled with `--no-reboot`. |

Anything answering again ends the episode, and that is logged with how
long it lasted.

The reload is recorded before it starts. If the driver hangs in the kernel,
the unit's 5-minute start timeout ends the check, and the next check moves
on to the reboot. If the driver does not come back, or comes back under
another interface name, that still counts as nothing answering, and the
reboot follows: that is the case a reboot is for.

The episode's minutes are counted in time since boot, not by the wall
clock. A Raspberry Pi has no real-time clock, and NTP can move its clock by
hours just after a boot.

After a reboot the clock starts again: the driver reload comes 10 minutes
later, and a second reboot only once 6 hours have passed since the first.
A router that is off for a whole day therefore costs four reboots, not a
loop. A reboot loses nothing, because with the uplink gone there is nothing
to measure.

The check only watches Wi-Fi. A host whose default route is a cable, or a
tunnel, is left alone. It also acts only on an interface it has seen work:
until one check has had an answer through it, a silent Wi-Fi starts
nothing, so a host whose Wi-Fi never worked here is not rebooted over it.
That same memory covers a host with no default route at all: a hung radio
can lose its association, and the DHCP lease with it. A wired host with its
cable out, or one where Wi-Fi was never set up, is left alone.

Each log line also records two clues for the analysis afterwards, neither
of which triggers anything:

- whether the radio was still associated to the access point;
- how many packets it received while the router was unreachable.

Associated, with zero packets received, is the deaf-radio signature from
[Wi-Fi uplink stats](wifi.md#the-signature). Not associated looks more like
the access point being gone.

### SmokePing

Each check finds this stack's SmokePing container by its Compose labels and
asks s6, the process supervisor inside it, whether the SmokePing service is
up, and for how long (`s6-svstat -o up,wantedup,updownfor
/run/service/svc-smokeping`). The container gets 5
minutes from its start; a normal start takes under two. If the service is
still not up on two checks in a row, the watchdog runs `smoking-pi restart
smokeping`. A service whose supervisor never started counts as down too.
If another `smoking-pi` command is changing the stack at that moment (an
`upgrade`, or the stack starting at boot), the watchdog skips that minute
rather than restart a container that command may be replacing.

At most three restarts in any six hours; after that it only logs, so a
SmokePing that fails for a real reason (a broken `Targets` file, say) is
left for you to read in `smoking-pi logs smokeping`. A container that is
stopped, or a stack that is down, is not checked: that was a decision, not
a failure. An image whose state the watchdog cannot read is noted once and
left alone.

A SmokePing that dies as it starts is the opposite case. s6 starts it again
every second or two, so it reads as up half the time, and a container
restart would start the same broken configuration again. When the service
changed state less than 30 seconds ago on two checks at least 50 seconds
apart, the watchdog logs, once per loop:

```text
smokeping: svc-smokeping keeps restarting 46 min after the container started: SmokePing exits at start, usually a configuration error, which a restart does not fix; not restarting (sudo smoking-pi logs smokeping)
```

and restarts nothing. `smoking-pi logs smokeping` shows the line SmokePing
refused (`ERROR: /config/General, line 17: table syntax error`, for
example).

## What it did

```bash
smoking-pi watchdog
```

```text
Watchdog: yes
  Wi-Fi: reload the driver after 10 min with nothing answering, reboot 5 min later (at most every 6 h)
  SmokePing: restart it when not running 5 min after its container started (at most 3 times in 6 h)

Last events (UTC):
  2026-10-13T03:12:00Z wifi: nothing answers on wlan0, neither router 192.168.1.1 nor the Internet (associated to aa:bb:cc:dd:ee:ff); watching
  2026-10-13T03:22:00Z wifi: nothing answers on wlan0 for 10 min (associated to aa:bb:cc:dd:ee:ff, 0 packets received meanwhile); reloading the brcmfmac driver
  2026-10-13T03:23:00Z wifi: wlan0 reaches the network again after 11 min
```

The events are kept in `/var/lib/smoking-pi/watchdog/events.log` (the last
200 lines), so they survive the reboot the watchdog may cause. Every line is
also in the unit's journal:

```bash
journalctl -u smoking-pi-watchdog
```

A reboot ends the journal of the boot that hung. To read that boot's kernel
messages afterwards (what the driver said before it went quiet), keep the
journal across reboots: see
[Keep the journal across reboots](wifi.md#keep-the-journal-across-reboots).

## Trying it without waiting for a hang

`--dry-run` makes a check say what it would do now, and do nothing: no
action, and nothing recorded, so it never changes what the timer does next:

```bash
sudo smoking-pi watchdog check --dry-run
```

To watch the whole sequence, cut the host off at the firewall from a
session that does not go through the router (a console, or SSH from another
machine on the same LAN). The rules are not saved, so the reboot clears
them:

```bash
sudo nft add table inet wdtest
sudo nft add chain inet wdtest out '{ type filter hook output priority 0; }'
sudo nft add rule inet wdtest out oifname wlan0 ip daddr != 192.168.1.0/24 drop
sudo nft add rule inet wdtest out ip daddr 192.168.1.1 drop
sudo nft add table arp wdtest
sudo nft add chain arp wdtest out '{ type filter hook output priority 0; }'
sudo nft add rule arp wdtest out arp daddr ip 192.168.1.1 drop
```

Replace `192.168.1.0/24` with your LAN and `192.168.1.1` with your router
(`ip -4 route show default`). The first rule cuts everything beyond the LAN,
the second cuts the router itself, and the third stops the host asking for
the router on the link (ARP). Other hosts on the LAN still reach
the Pi, because that traffic does not go through the router. The driver
reload does not clear the rules. The reboot that follows does, and that is
the recovery to look for. Blocking less is not enough: the Internet, or the
router's link-level answer, keeps the watchdog quiet, as it should. The
packet count in the log keeps climbing during the rehearsal, because the
radio still receives. In a real hang it stays at zero.
