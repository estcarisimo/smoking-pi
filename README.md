# Smoking Pi: SmokePing‑on‑Containers 🖥️📈

A modular, multi‑flavour toolkit for running **SmokePing** latency monitoring — from a single‑container Raspberry Pi build to a full Grafana / InfluxDB stack and, eventually, a Helm chart for Kubernetes.

```text
.
├── minimal/           # lightweight Dockerfile (SmokePing + Lighttpd only)
├── grafana-influx/    # docker‑compose stack (InfluxDB v2 + Grafana)  ← WIP
├── kubernetes/        # Helm chart & manifests (Ingress, PVCs, Prometheus) ← roadmap
└── docs/              # architecture diagrams, HOWTOs shared by all variants
```

---

## 1  Quick start ( minimal variant )

```bash
git clone https://github.com/<your‑user>/smoking‑pi.git
cd smoking‑pi/minimal
docker build -t smokeping:mini .
docker run -d --name smokeping -p 80:80 smokeping:mini
# browse:
open http://<docker‑host>/cgi-bin/smokeping.cgi
```

<details>
<summary>Persist data (named volumes)</summary>

```bash
docker volume create smokeping_rrd
docker volume create smokeping_cache

docker run -d --name smokeping \
  -v smokeping_rrd:/var/lib/smokeping \
  -v smokeping_cache:/var/cache/smokeping \
  -p 80:80 smokeping:mini
```

</details>

---

## 2  Repository structure

| Folder                | Status         | What’s inside                                                |
| --------------------- | -------------- | ------------------------------------------------------------ |
| **`minimal/`**        | ✓ stable       | Debian Slim + SmokePing + Lighttpd + fping. Ideal for Pi/VM. |
| **`grafana-influx/`** | 🚧 in progress | `docker‑compose.yml` with InfluxDB v2 & Grafana dashboards.  |
| **`kubernetes/`**     | 📝 roadmap     | Helm chart (StatefulSet, Ingress, TLS, Prometheus).          |
| **`docs/`**           | ↘ shared       | Diagrams, ADRs, HOWTOs common to every flavour.              |

All variants reuse the same **SmokePing config fragments** (`config/Targets`, probes, alerts), so you can lift‑and‑shift without rewrites.


---

## 3  Contributing

1. Fork → feature branch → PR.
2. Pre‑commit hooks: `shellcheck`, `hadolint`.
3. CI builds for arm64 & amd64 run `smokeping --debug` self‑test.

See **`docs/CONTRIBUTING.md`** for style guide and DCO sign‑off.

---

## 4  License

Choose either **MIT** or **Apache‑2.0** and update `LICENSE` accordingly.

---

> **Tip:** Need only “ping a few hosts and get graphs”? Keep using **`minimal/`**. When you’re ready for long‑term retention and dashboards, copy your `config/` folder into **`grafana‑influx/`** and run `docker‑compose up ‑d`.
