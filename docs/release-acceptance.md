# Release acceptance on a Raspberry Pi

The release checklist: what a release has to show beyond the checks every
PR already passes, the order it is cut in, what it has to show on real
Raspberry Pi hardware before it is tagged, and how the result is recorded. The release workflow proves the
package on Ubuntu VMs and Debian containers ([Packaging → Supported
hosts](packaging.md#supported-hosts)); none of that is a Pi. The kernel, the
Wi-Fi driver, the first hop, boot ordering against `docker.service`, and
whether the measurements are still right are only shown here.

Until there is a second Pi registered as a self-hosted runner, this is a
manual gate on the **reference Pi** — which is also the production monitor.
That is a known compromise: the roadmap asks for the candidate to be
isolated from the reference monitor's data, and this procedure gets there
only through the backup and the rollback path below. Do not skip them.

## What a release proves that a PR does not

Every PR already runs every check that *can* run on a PR, and they are
required: lint, shell syntax, the module and CLI tests, the three editions'
Compose, Grafana provisioning, the docs build, the apt repository and the
Homebrew formula, all nine images built for arm64 and amd64, and CodeQL
([CONTRIBUTING](https://github.com/estcarisimo/smoking-pi/blob/main/CONTRIBUTING.md)).
Nothing is moved from a PR to the release to save time. A release adds
only what a release alone can show:

| Only a release shows | Where |
|---|---|
| The nine images **published** to GHCR as one multi-arch manifest each, from the tagged commit | `release.yml`: `build`, `merge` |
| The `.deb` built from the tagged tree installs with each supported host's own `apt` | `release.yml`: `package`, `debian` (Debian 12/13 containers) |
| It installs, starts Basic with those images, and upgrades from the previous release keeping its secrets | `release.yml`: `host` (Ubuntu VMs) |
| Its data survives: `backup`, `purge --config` and `restore` onto a card with no edition recorded bring back the same secrets, config and data; `apt purge` and a reinstall bring back the same stack | `release.yml`: `host` (`check-package.sh`, steps 6b and 8) |
| It comes back by itself when Docker is restarted, or stopped and started (a Docker package upgrade), under the enabled unit | `release.yml`: `host` (`check-package.sh`, step 6c) |
| It works on a Raspberry Pi: the kernel, the Wi-Fi driver, the first hop, boot order, the measurements | this checklist, on the reference Pi |
| It stays up: 24 hours with no restart and no unexplained gap | this checklist, *Stability* |
| `latest` points at it — only after every install test passed | `release.yml`: `promote` |
| What was shipped, tied to the commit: digests, package checksum, the run | the evidence file `attach` puts on the release |

## Releasing, step by step

A release is cut from a **candidate**: a `vX.Y.Z-rc.N` tag runs the
whole release workflow and produces exactly the artifacts the release will
ship, so the Pi is tested on those, not on a clone. The final tag goes on
the same commit.

1. **Prepare.** A `release/vX.Y.Z` branch converts `[Unreleased]` into
   the dated section (with its intro), bumps `version` and `date-released`
   in `CITATION.cff`. PR, CI green, review, merge. The date is the day
   the candidate is cut: the release is that same commit, so it cannot
   change later.
2. **Tag the candidate** on that merge commit: `git tag -a vX.Y.Z-rc.1 -m
   "vX.Y.Z candidate 1" && git push origin vX.Y.Z-rc.1`, then `gh release
   create vX.Y.Z-rc.1 --prerelease --verify-tag --title "vX.Y.Z-rc.1" --notes
   "Candidate for vX.Y.Z."`. It **must** be a pre-release: the apt repository
   and the upgrade test skip pre-releases, and `attach` refuses a candidate
   on anything else.
3. **Watch the Release run.** Every `host` and `debian` job green; `attach`
   puts the package and `smoking-pi_X.Y.Z-rc.1_evidence.md` on the
   pre-release. The package's version is `X.Y.Z~rc.1` (so apt sorts it
   before `X.Y.Z`), but GitHub replaces `~` in an asset name with `.`: the
   file to download is `smoking-pi_X.Y.Z.rc.1_all.deb`. `latest` does not
   move and the site is not redeployed for a candidate.
4. **Accept it on the Pi**, below, on the candidate's own artifacts. A
   package install: `sudo apt install ./smoking-pi_X.Y.Z.rc.1_all.deb`
   (apt reads the version from inside the file, not its name, so it still
   installs `X.Y.Z~rc.1`), then `sudo smoking-pi upgrade`. A clone (the
   reference Pi): `git checkout vX.Y.Z-rc.1`, then
   `SMOKING_PI_VERSION=X.Y.Z-rc.1 smoking-pi upgrade`, which
   pulls the published images instead of building. Keep
   that variable set for every command until the release: without it a
   clone means `dev` and builds. Start the 24-hour stability clock.
5. **Anything found** is fixed through a normal PR; tag `vX.Y.Z-rc.2` on
   the new merge and start again from step 3. Candidates are never deleted:
   they are the record of what was tried.
6. **Tag the release on the accepted candidate's commit**: `git tag -a
   vX.Y.Z <sha of vX.Y.Z-rc.N> -m "vX.Y.Z"`, push, `gh release create vX.Y.Z
   --verify-tag` with the notes and the **Validation** section below. The run
   rebuilds from the same commit, runs every install test again, moves
   `latest`, and attaches the `.deb` and the evidence; the docs site and
   `/apt` follow (`docs.yml`).
7. **After:** the Homebrew bump (`packaging/homebrew/bump.sh vX.Y.Z`, a
   PR), the roadmap's release row, the posts.

## Before

- [ ] The candidate is a `vX.Y.Z-rc.N` tag on a merged `main` (every PR
  reviewed, CI green) whose Release run is green, with `[Unreleased]`
  converted into the dated section and `CITATION.cff` bumped.
- [ ] `smoking-pi backup` completed and
  the directory is somewhere other than the Pi's SD card. Note its path in
  the record.
- [ ] `shared/scripts/acceptance-record.sh --tag X.Y.Z-rc.N` on the Pi,
  from the live checkout (`SMOKING_PI_VERSION` or an exact git tag also
  name the candidate). It prints the *Validation* block below with the tag, commit, Pi
  model, OS, Debian release, kernel and architecture filled in, plus each
  container's image, restart count and uptime and the doctor's summary. It
  also names any container that is not on the candidate's image tag. The
  rest stays `<fill>`. Run it again at the end of the 24 hours for the
  *Stability* line.
- [ ] The rollback is one command away: the previous tag checked out and
  `smoking-pi upgrade`, or the backup restored. Write down which.

## Clean install and upgrade

The reference Pi is always an **upgrade**; a clean install is proven by the
release workflow's `host` jobs (Ubuntu VMs) and, on Raspberry Pi OS, by the
`debian` containers at the package level only. Once a spare Pi exists, a
clean install there is the missing half — until then the record says
"clean install on Raspberry Pi OS: untested".

- [ ] The candidate's own artifacts (*Releasing*, step 4) and `smoking-pi
  upgrade` — with a version set, that is `compose pull` then `up -d
  --remove-orphans`, the doctor `--live` at the end. Check every container
  runs a `:X.Y.Z-rc.N` image (`docker compose images`): a `:dev` one was
  built, not the candidate. Note the wall time and whether any container
  restarted more than once (`docker compose ps`, `docker compose logs
  --since 10m | grep -i error`).
- [ ] Configuration and credentials survived: `smoking-pi passwords
  --show-secrets` shows the same values as before, the target list in the
  web admin is intact,
  Grafana's dashboards still show history older than the upgrade.

## Startup and recovery

- [ ] `sudo reboot`. Within five minutes: `systemctl status smoking-pi` (a
  package) or `docker compose ps` — every service that was up before is up,
  none restarting. `docker compose logs --since 10m` has no repeated
  errors.
- [ ] Docker's own start order held: the stack came up **after**
  `docker.service` and the network (`journalctl -b -u smoking-pi`, or the
  first lines of each container's log).

## Service health and data

- [ ] `doctor --live` reports `0 fail` (and says why for every warn):
  every deployed `.py` matches the tree, the panels' measurements are being
  written, the alerter's defaults match.
- [ ] Data is arriving: in Grafana, the latency panels have points in the
  last five minutes for every probe the edition enables (ICMP, DNS,
  HTTP/1.1–3, TCP, the CPE hop, the Wi-Fi link — [Measuring](index.md)).
- [ ] The web admin (Standard/Pro) lists the targets and can toggle one;
  the change appears in `smokeping` within a probe cycle and is toggled
  back.
- [ ] Wi-Fi stats ([Wi-Fi uplink stats](wifi.md)): the panel shows
  signal and rate for the uplink, and the radio has not hung since boot
  ([When the radio hangs](wifi.md#when-the-radio-hangs)).

## Detections and integrations

- [ ] Downtime and microcut detection: `get_loss_events` and
  `get_microcut_stats` over the last 24 h return without error and their
  classifications agree with the alerter's rules
  ([Detection reliability](detection-reliability.md)). If the day had no
  event, say so — do not fabricate one.
- [ ] Every profile the Pi runs answers: the MCP server (`get_chart`, one
  question through OpenClaw), the alerter (`NOTIFY_MODE` as configured, a
  dry-run notification if the mode allows), AI insights if enabled.

## Editions and backends

The reference Pi runs one combination (Pro, InfluxDB, `alerts,mcp`
profiles). Everything else is **untested on a Pi** unless the record says
otherwise: Basic and Standard, ClickHouse, the `ai` profile. The release
workflow starts Basic on Ubuntu; Standard and Pro are rendered on every
host but started only here. Write the combinations *not* exercised into
the record — the reader should never infer coverage.

## Stability

- [ ] The candidate stays on the reference Pi for at least **24 hours**
  before the tag, through at least one full day-night cycle of Wi-Fi
  conditions. Acceptance: no container restarted on its own (`docker
  inspect --format '{{.RestartCount}}'` is 0 for all), the doctor still
  passes, the panels have no gaps that the loss events do not explain.
- [ ] Anything found is fixed on a branch and the clock restarts; the
  record lists what was found.

## Rollback

If any item fails and is not a documentation error: `git checkout
<previous tag> && smoking-pi upgrade` (a clone), or `apt install` of the
previous `.deb`, then `smoking-pi restore <backup>` only if the data was
touched. Record what failed, and the release waits.

## The record

Every release gets a **Validation** section in its GitHub release notes
(and a row in the roadmap's release table), filled from the checklist:

```markdown
## Validation

- Tag / commit: vX.Y.Z / <sha> — accepted as vX.Y.Z-rc.N (same commit)
- Artifacts: images ghcr.io/estcarisimo/smoking-pi/*:X.Y.Z, smoking-pi_X.Y.Z_all.deb; digests and checksum in smoking-pi_X.Y.Z_evidence.md (release assets)
- Release workflow: <run URL> — host 5/5, debian 4/4, upgrade from vX.Y.(Z-1): pass | none yet; candidate run: <run URL>
- Reference Pi: <model>, Raspberry Pi OS <version> (Debian N), kernel <uname -r>, arm64
- Upgrade on the Pi from vX.Y.(Z-1): pass — <minutes>, restarts 0; credentials and targets intact
- Reboot recovery: pass — up in <minutes>
- doctor --live: <n> ok, <n> warn (why), 0 fail
- Data: all enabled probes writing; Wi-Fi panel live; no radio hang since boot
- Detections: get_loss_events / get_microcut_stats agree with the alerter; events that day: <what>
- Stability: <hours> on the Pi, restarts 0
- Untested this release: clean install on Raspberry Pi OS; Basic/Standard on a Pi; ClickHouse; ai profile
- Known limitations: <anything found and deferred, with the issue link>
- Backup used: <path>; rollback path: <which>
```

"Untested" is a valid entry. A release with an honest untested list is
acceptable; one with an implied coverage is not.
