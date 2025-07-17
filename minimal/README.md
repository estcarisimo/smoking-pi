# SmokePing Docker

This repository provides a minimal Docker image for running **SmokePing**, a latency‑monitoring tool.

The container bundles:

* **SmokePing** – collects round‑trip latency measurements and generates graphs
* **fping** – default ICMP probe
* **spawn‑fcgi** & **fcgiwrap** – FastCGI bridge for CGI scripts
* **lighttpd** – lightweight web server that serves the SmokePing interface

---

## Table of Contents

0. [Prerequisites](#0-prerequisites)
1. [Installing Docker & Docker Compose on Raspberry Pi](#1-installing-docker--docker-compose-on-raspberry-pi)
2. [TL;DR — Quick Start](#2-tldr--quick-start)
3. [Building the Docker Image](#3-building-the-docker-image)
4. [Running the Container](#4-running-the-container)
5. [Customizing the SmokePing Configuration](#5-customizing-the-smokeping-configuration)
6. [Storing and Preserving RRD Data](#6-storing-and-preserving-rrd-data)
7. [Accessing the Web Interface](#7-accessing-the-web-interface)
8. [Troubleshooting](#8-troubleshooting)
9. [Additional Resources](#9-additional-resources)

---

## 0. Prerequisites

1. **Docker Engine 20.10+** and **Docker Compose v2** (plugin‑based) installed on the host.
2. A Raspberry Pi 3/4/5 running a recent 64‑bit Raspberry Pi OS (Debian 12) *or* any x86‑64 Linux/macOS machine.
3. (Optional) Familiarity with **SmokePing** configuration to customize targets and alerts.

---

## 1. Installing Docker & Docker Compose on Raspberry Pi

> Skip this section if Docker is already working on your Pi.

### Tested hardware / OS

* Raspberry Pi OS 64‑bit (Debian 12 "bookworm")
* Pi 4 Model B / Pi 3 Model B+

### 1.1 Install Docker Engine

```bash
# Run the official convenience script (installs docker‑ce for ARM)
curl -fsSL https://get.docker.com | sudo sh

# Add your user to the "docker" group so sudo isn't required
sudo usermod -aG docker $USER
newgrp docker   # or simply log out & back in
```

> The script enables and starts the **docker** service automatically.

### 1.2 Install Docker Compose (plugin)

```bash
sudo apt-get update
sudo apt-get install -y docker-compose-plugin
```

Verify the installation:

```bash
docker compose version  # should print v2.x
```

### 1.3 Sanity check

```bash
docker run --rm hello-world
```

If you see *"Hello from Docker!"* your setup is good to go.

---

## 2. TL;DR — Quick Start

```bash
# Clone the repo
git clone https://github.com/estcarisimo/smoking-pi.git
cd smoking-pi/

docker build -t smokeping:latest .
docker run -d -p 80:80 --name smokeping smokeping:latest
```

Browse to **http\://\<pi‑ip>/cgi-bin/smokeping.cgi** — you should see SmokePing graphs start populating within a few minutes.

to terminate it
```bash
docker stop smokeping       # container exits
docker rm smokeping         # now it’s gone
```


---

## 3. Building the Docker Image

1. Open a terminal in the root of this repository.
2. Build the multi‑arch image (buildx handles ARM & x86):

```bash
docker buildx build --platform linux/arm64,linux/amd64 -t smokeping:latest .
```

*(On the Pi itself you can omit `--platform`.)*

---

## 4. Running the Container

```bash
docker run -d --name my_smokeping -p 80:80 smokeping:latest
```

| Flag                  | Meaning                              |
| --------------------- | ------------------------------------ |
| `-d`                  | Detached mode                        |
| `--name my_smokeping` | Friendly container name              |
| `-p 80:80`            | Map host port 80 → container port 80 |

### Verify

```bash
docker ps
```

You should see **my\_smokeping** in the list.

---

## 5. Customizing the SmokePing Configuration

The image ships with **`/etc/smokeping/config`** copied from **`config/smokeping_config`** in the repo.
To override it at runtime:

```bash
docker run -d \
  --name my_smokeping \
  -v /path/to/override_config:/etc/smokeping/config \
  -p 80:80 \
  smokeping:latest
```

Remember to `docker restart my_smokeping` after editing the file.

---

## 6. Storing and Preserving RRD Data

SmokePing saves its RRDs under */var/lib/smokeping*.

```bash
docker run -d \
  --name my_smokeping \
  -v /host/smokeping_config:/etc/smokeping/config \
  -v /host/smokeping_data:/var/lib/smokeping \
  -p 80:80 \
  smokeping:latest
```

---

## 7. Accessing the Web Interface

Open a browser:

```text
http://<HOST_IP>/cgi-bin/smokeping.cgi
```

> If running locally on the Pi, `http://localhost/cgi-bin/smokeping.cgi` works.

---

## 8. Troubleshooting

### 8.1 No data collected

* Does `fping` work inside the container?
  `docker exec -it my_smokeping fping 8.8.8.8`
* Firewall or network blocks ICMP?
* Check container logs: `docker logs my_smokeping`

### 8.2 Web interface unreachable

* `docker ps` — is the container up?
* Correct port mapping?
* SELinux/AppArmor blocking volume mount?

---

## 9. Additional Resources

* SmokePing docs → [https://oss.oetiker.ch/smokeping/doc/](https://oss.oetiker.ch/smokeping/doc/)
* Docker Engine ARM guide → [https://docs.docker.com/engine/install/debian/#install-using-the-convenience-script](https://docs.docker.com/engine/install/debian/#install-using-the-convenience-script)
* Docker Compose plugin → [https://docs.docker.com/compose/install/linux/#install-using-the-repository](https://docs.docker.com/compose/install/linux/#install-using-the-repository)

---

### Maintainer

**Esteban Carisimo**

### License

Add your preferred license here (MIT, Apache‑2.0, etc.).
