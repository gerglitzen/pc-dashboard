#!/usr/bin/env python3
"""LAN system dashboard: serves index.html and a 1 Hz Server-Sent Events stream of system stats."""
import glob
import json
import os
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psutil

PORT = int(os.environ.get("PORT", 8765))
HERE = os.path.dirname(os.path.abspath(__file__))
VIRTUAL_NICS = ("lo", "docker", "veth", "br-", "virbr")

cond = threading.Condition()
latest = "{}"


def net_bytes():
    sent = recv = 0
    for name, c in psutil.net_io_counters(pernic=True).items():
        if not name.startswith(VIRTUAL_NICS):
            sent += c.bytes_sent
            recv += c.bytes_recv
    return sent, recv


def cpu_mhz():
    # read sysfs directly: psutil 5.9.0 (Ubuntu's python3-psutil) reports GHz instead of MHz
    paths = glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq")
    return sum(int(open(p).read()) for p in paths) / len(paths) / 1000


def gpu():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2).stdout.splitlines()[0]
        name, temp, util, mu, mt = [x.strip() for x in out.split(",")]
        return {"name": name, "temp": float(temp), "util": float(util),
                "mem_used": float(mu), "mem_total": float(mt)}
    except Exception:  # no NVIDIA GPU / driver asleep
        return None


def top_procs():
    """Top 5 process names by CPU and by RAM; processes with the same name are summed."""
    ncpu = psutil.cpu_count()
    groups = {}
    for p in psutil.process_iter(["name", "cpu_percent", "memory_info"]):
        i = p.info
        g = groups.setdefault(i["name"], [0.0, 0, 0])
        g[0] += (i["cpu_percent"] or 0) / ncpu  # % of whole CPU, like the total
        g[1] += i["memory_info"].rss if i["memory_info"] else 0
        g[2] += 1

    def top(k):
        rows = sorted(groups.items(), key=lambda x: -x[1][k])[:5]
        return [[name, v[k], v[2]] for name, v in rows]

    return {"cpu": top(0), "mem": top(1)}


def sample(dt, net, prev_net, disk, prev_disk, g, procs):
    temps = psutil.sensors_temperatures()
    cpu_temp = next((s.current for s in temps.get("coretemp", []) if s.label.startswith("Package")), None)
    nvme_temp = next((s.current for s in temps.get("nvme", [])), None)
    vm = psutil.virtual_memory()
    bat = psutil.sensors_battery()
    cores = psutil.cpu_percent(percpu=True)
    return {
        "host": socket.gethostname(),
        "uptime": time.time() - psutil.boot_time(),
        "cpu": sum(cores) / len(cores),
        "cores": cores,
        "freq": cpu_mhz(),
        "load": os.getloadavg(),
        "cpu_temp": cpu_temp,
        "nvme_temp": nvme_temp,
        "fans": [{"label": s.label or k, "rpm": s.current}
                 for k, v in psutil.sensors_fans().items() for s in v],
        "ram_used": vm.total - vm.available,
        "ram_total": vm.total,
        "swap": psutil.swap_memory().percent,
        "net_up": (net[0] - prev_net[0]) / dt,
        "net_down": (net[1] - prev_net[1]) / dt,
        "disk_read": (disk.read_bytes - prev_disk.read_bytes) / dt,
        "disk_write": (disk.write_bytes - prev_disk.write_bytes) / dt,
        "disk_used": psutil.disk_usage("/").percent,
        "gpu": g,
        "procs": procs,
        "bat": {"pct": bat.percent, "plugged": bat.power_plugged} if bat else None,
    }


def sampler():
    global latest
    psutil.cpu_percent(percpu=True)  # prime the counter
    prev_net, prev_disk, prev_t = net_bytes(), psutil.disk_io_counters(), time.time()
    top_procs()  # prime per-process CPU counters
    g, procs, tick = None, None, 0
    while True:
        time.sleep(1)
        now = time.time()
        net, disk = net_bytes(), psutil.disk_io_counters()
        if tick % 2 == 0:  # nvidia-smi and the process scan are slow-ish; every 2 s is plenty
            g = gpu()
            procs = top_procs()
        tick += 1
        data = json.dumps(sample(now - prev_t, net, prev_net, disk, prev_disk, g, procs))
        prev_net, prev_disk, prev_t = net, disk, now
        with cond:
            latest = data
            cond.notify_all()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                while True:
                    with cond:
                        cond.wait(timeout=5)
                        data = latest
                    self.wfile.write(("data: " + data + "\n\n").encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
        elif self.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    threading.Thread(target=sampler, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    print(f"Dashboard on http://0.0.0.0:{PORT}  (open http://<this-pc-ip>:{PORT} on the iPad)")
    server.serve_forever()
