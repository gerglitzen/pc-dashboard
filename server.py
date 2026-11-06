#!/usr/bin/env python3
"""LAN system dashboard: serves index.html, a 1 Hz Server-Sent Events stream of system stats,
and a small task board stored in a markdown file (Done tasks are archived daily)."""
import glob
import json
import os
import re
import socket
import subprocess
import threading
import time
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psutil
import yaml

PORT = int(os.environ.get("PORT", 8765))
HERE = os.path.dirname(os.path.abspath(__file__))
VIRTUAL_NICS = ("lo", "docker", "veth", "br-", "virbr")
TASKS_FILE = os.path.expanduser(os.environ.get("TASKS_FILE", os.path.join(HERE, "tasks.md")))
ARCHIVE_DIR = os.path.join(os.path.dirname(TASKS_FILE), "archive")
try:
    CONFIG = yaml.safe_load(open(os.path.join(HERE, "config.yaml"), encoding="utf-8")) or {}
except FileNotFoundError:
    CONFIG = {}
ARCHIVE_RETENTION_DAYS = int(CONFIG.get("archive_retention_days", 30))
PRIOS = ("High", "Medium", "Low")
# trailing time log, e.g. "(9:00-10:00; 10:50-)"; an open end means the timer is running
PRIO_RE = re.compile(r"\s+!(high|medium|low)\s*$", re.I)  # priority tag on Done lines
TIMES_RE = re.compile(r"\s*\((\d{1,2}:\d{2}-(?:\d{1,2}:\d{2})?(?:;\s*\d{1,2}:\d{2}-(?:\d{1,2}:\d{2})?)*)\)\s*$")
tasks_lock = threading.Lock()

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


def read_tasks():
    """Parse the task file into [{title, status: backlog|progress|done, prio, times: [[start, end]]}]."""
    try:
        lines = open(TASKS_FILE, encoding="utf-8").read().splitlines()
    except FileNotFoundError:
        return []
    tasks, status, prio = [], "backlog", "Medium"
    for line in lines:
        s = line.strip()
        if s.startswith("# "):
            h = s.lower()
            status = "done" if "done" in h else "progress" if "prog" in h else "backlog"
            prio = "Medium"
        elif s.startswith("## "):
            prio = s[3:].strip().capitalize()
            prio = prio if prio in PRIOS else "Medium"
        elif s[:3] in ("[ ]", "[x]", "[X]"):
            title, times = s[3:].strip(), []
            m = TIMES_RE.search(title)
            if m:
                title = title[:m.start()]
                times = [r.strip().split("-") for r in m.group(1).split(";")]
            p = PRIO_RE.search(title)
            if p:
                title = title[:p.start()]
            tasks.append({"title": title, "status": status, "prio": p.group(1).capitalize() if p else prio, "times": times})
    return tasks


def write_tasks(tasks):
    def items(status, prio=None):
        out = []
        for t in tasks:
            if t["status"] == status and (prio is None or t["prio"] == prio):
                times = "; ".join(a + "-" + b for a, b in t["times"])
                out.append("[%s] %s%s%s" % ("x" if status == "done" else " ", " ".join(t["title"].split()),
                                            "" if prio else " !" + t["prio"], " (%s)" % times if times else ""))
        return out

    lines = []
    for head, status in (("Backlog", "backlog"), ("In progress", "progress")):
        lines += ["# " + head]
        for p in PRIOS:
            lines += ["## " + p] + items(status, p) + [""]
        lines += [""]
    lines += ["# Done"] + items("done")
    tmp = TASKS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, TASKS_FILE)


def archive_done():
    """If the task file was last changed before today, move its Done tasks to archive/<that day>.jsonl."""
    try:
        day = date.fromtimestamp(os.stat(TASKS_FILE).st_mtime)
    except FileNotFoundError:
        return
    if day >= date.today():
        return
    tasks = read_tasks()
    done = [t for t in tasks if t["status"] == "done"]
    if not done:
        return
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    with open(os.path.join(ARCHIVE_DIR, day.isoformat() + ".jsonl"), "a", encoding="utf-8") as f:
        for t in done:
            f.write(json.dumps({"date": day.isoformat(), "title": t["title"], "times": t["times"]},
                               ensure_ascii=False) + "\n")
    write_tasks([t for t in tasks if t["status"] != "done"])


def prune_archive():
    """Delete archive/YYYY-MM-DD.jsonl files older than ARCHIVE_RETENTION_DAYS."""
    cutoff = date.today() - timedelta(days=ARCHIVE_RETENTION_DAYS)
    try:
        names = os.listdir(ARCHIVE_DIR)
    except FileNotFoundError:
        return
    for name in names:
        try:
            day = date.fromisoformat(name.removesuffix(".jsonl"))
        except ValueError:
            continue
        if name.endswith(".jsonl") and day < cutoff:
            os.remove(os.path.join(ARCHIVE_DIR, name))


def archiver():
    while True:
        with tasks_lock:
            archive_done()
        prune_archive()
        time.sleep(60)


def tasks_state():
    try:
        version = str(os.stat(TASKS_FILE).st_mtime_ns)
    except FileNotFoundError:
        version = "0"
    return {"version": version, "tasks": read_tasks()}


class Handler(BaseHTTPRequestHandler):
    def send_json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_PUT(self):
        """Replace the whole task list; 409 if the file changed since the client last read it."""
        if self.path != "/tasks":
            return self.send_error(404)
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with tasks_lock:
            state = tasks_state()
            if body["version"] != state["version"]:
                return self.send_json(409, state)
            write_tasks(body["tasks"])
            self.send_json(200, tasks_state())

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
        elif self.path == "/tasks":
            self.send_json(200, tasks_state())
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
    threading.Thread(target=archiver, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    print(f"Dashboard on http://0.0.0.0:{PORT}  (open http://<this-pc-ip>:{PORT} on the iPad)")
    server.serve_forever()
