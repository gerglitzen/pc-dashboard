#!/usr/bin/env python3
"""LAN system dashboard: serves index.html, a 1 Hz Server-Sent Events stream of system stats,
and a small task board stored in a JSON file (Done tasks are archived daily).
Tickets can be linked to a Clockify task; their finished time ranges are logged to Clockify.
My Notion tickets come to the board daily (or on Sync), and moving a ticket can set its Notion status."""
import glob
import json
import os
import re
import socket
import subprocess
import threading
import time
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psutil
import yaml

import clockify
import notion

PORT = int(os.environ.get("PORT", 8765))
HERE = os.path.dirname(os.path.abspath(__file__))


def load_env(path):
    """Put the KEY=value lines of a .env file into os.environ (real environment variables win)."""
    try:
        for line in open(path, encoding="utf-8"):
            k, sep, v = line.strip().partition("=")
            if sep and not k.startswith("#"):
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))
    except FileNotFoundError:
        pass


load_env(os.path.join(HERE, ".env"))
VIRTUAL_NICS = ("lo", "docker", "veth", "br-", "virbr")
TASKS_FILE = os.path.expanduser(os.environ.get("TASKS_FILE", os.path.join(HERE, "tasks.json")))
ARCHIVE_DIR = os.path.join(os.path.dirname(TASKS_FILE), "archive")
try:
    CONFIG = yaml.safe_load(open(os.path.join(HERE, "config.yaml"), encoding="utf-8")) or {}
except FileNotFoundError:
    CONFIG = {}
ARCHIVE_RETENTION_DAYS = int(CONFIG.get("archive_retention_days", 30))
PRIOS = ("High", "Medium", "Low")
GEN_RE = re.compile(r"^([A-Z]+-\d+):\s*")  # Clockify task names start with their Notion ticket: "GEN-4544:..."
tasks_lock = threading.Lock()
clockify_error = None  # last Clockify logging error, shown on the board
notion_error = None  # last Notion sync error, shown on the board
sync_lock = threading.Lock()  # one Notion sync at a time (it may wait a minute for Clockify tasks)

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


def read_state():
    """The task file: {notion_day: "YYYY-MM-DD" of the last Notion sync, tasks: [...]}."""
    try:
        with open(TASKS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"notion_day": "", "tasks": []}


def read_tasks():
    """The task list in board order:
    [{title, status: backlog|progress|done, prio: High|Medium|Low, clockify: "projectId/taskId" or "",
      notion: "GEN-<n>" or "", role: my role on the Notion ticket, "assignee" | "reviewer" | "" (unknown / none),
      times: [[start "H:MM", end "H:MM" ("" = timer running), Clockify entry id or "", "YYYY-MM-DD" the range started]]}]"""
    return [dict({"notion": "", "role": ""}, **t) for t in read_state()["tasks"]]


def write_tasks(tasks, notion_day=None):
    state = {"notion_day": notion_day or read_state().get("notion_day", ""), "tasks": tasks}
    tmp = TASKS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, TASKS_FILE)


def migrate_md():
    """One-time move from the old markdown task file: if there is no task file yet but a tasks.md next to it,
    convert it (keeping its date, which the archive uses) and rename it to tasks.md.bak."""
    md = os.path.join(os.path.dirname(TASKS_FILE), "tasks.md")
    if os.path.exists(TASKS_FILE) or not os.path.exists(md):
        return
    rng = r"(?:\d{4}-\d{2}-\d{2}\s+)?\d{1,2}:\d{2}-(?:\d{1,2}:\d{2}(?:✓[0-9a-fA-F]+)?)?"
    times_re = re.compile(r"\s*\((%s(?:;\s*%s)*)\)\s*$" % (rng, rng))  # "(2026-11-06 9:00-10:00✓<entryId>; 10:50-)"
    prio_re = re.compile(r"\s+!(high|medium|low)\s*$", re.I)  # priority tag on Done lines
    link_re = re.compile(r"\s*\{cf:([0-9a-fA-F]+/[0-9a-fA-F]+)\}\s*$")
    st = os.stat(md)
    day = date.fromtimestamp(st.st_mtime).isoformat()  # for ranges without a date
    tasks, status, prio = [], "backlog", "Medium"
    for line in open(md, encoding="utf-8").read().splitlines():
        s = line.strip()
        if s.startswith("# "):
            h = s.lower()
            status = "done" if "done" in h else "progress" if "prog" in h else "backlog"
            prio = "Medium"
        elif s.startswith("## "):
            prio = s[3:].strip().capitalize()
            prio = prio if prio in PRIOS else "Medium"
        elif s[:3] in ("[ ]", "[x]", "[X]"):
            title, times, link = s[3:].strip(), [], ""
            m = times_re.search(title)
            if m:
                title = title[:m.start()]
                for r in m.group(1).split(";"):
                    d, _, r = r.strip().rpartition(" ")
                    start, end = r.split("-")
                    end, _, entry = end.partition("✓")
                    times.append([start, end, entry, d.strip() or day])
            p = prio_re.search(title)
            if p:
                title = title[:p.start()]
            m = link_re.search(title)
            if m:
                title, link = title[:m.start()], m.group(1)
            tasks.append({"title": " ".join(title.split()), "status": status,
                          "prio": p.group(1).capitalize() if p else prio, "clockify": link, "times": times})
    write_tasks(tasks)
    os.utime(TASKS_FILE, ns=(st.st_atime_ns, st.st_mtime_ns))
    os.rename(md, md + ".bak")
    print(f"Converted {md} to {TASKS_FILE} (the old file is now tasks.md.bak)")


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


def merge_ids(tasks, old):
    """Give back the entry ids a client hasn't seen yet (logged by a retry after its last load),
    matched by link + range, so those ranges aren't logged twice."""
    have = {r[2] for t in tasks for r in t["times"]}
    free = {}
    for t in old:
        for r in t["times"]:
            if r[2] and r[2] not in have:
                free.setdefault((t["clockify"], r[3], r[0], r[1]), []).append(r[2])
    for t in tasks:
        for r in t["times"]:
            ids = free.get((t["clockify"], r[3], r[0], r[1]))
            if not r[2] and ids:
                r[2] = ids.pop(0)


def range_times(r):
    """A finished range as aware datetimes, starting on its date (an end before the start means it ended the day after)."""
    day = date.fromisoformat(r[3])
    start, end = (datetime.combine(day, datetime.strptime(x, "%H:%M").time()).astimezone() for x in r[:2])
    return start, (end + timedelta(days=1) if end < start else end)


def sync_changes(tasks, old):
    """Mirror changes of logged ranges to Clockify: a removed range (or ticket) deletes its entry; a changed
    range (time or date), link or title updates it. Raises ClockifyError on the first failure."""
    before = {r[2]: (t["clockify"], t["title"], r[0], r[1], r[3]) for t in old for r in t["times"] if r[2]}
    now = {r[2]: (t, r) for t in tasks for r in t["times"] if r[2]}
    for entry in before.keys() - now.keys():
        clockify.delete_entry(entry)
    for entry, (t, r) in now.items():
        was = before.get(entry)
        if was and was != (t["clockify"], t["title"], r[0], r[1], r[3]) and t["clockify"] and r[1]:
            project_id, task_id = t["clockify"].split("/")
            clockify.update_entry(entry, project_id, task_id, t["title"], *range_times(r))


def log_pending(tasks):
    """Log the finished, not yet logged ranges of linked tasks to Clockify (on their own date)
    and store the entry ids. True if any was logged."""
    global clockify_error
    changed = False
    for t in tasks:
        if not t["clockify"]:
            continue
        project_id, task_id = t["clockify"].split("/")
        for r in t["times"]:
            if not r[1] or r[2] or r[0] == r[1]:
                continue
            try:
                r[2] = clockify.add_entry(project_id, task_id, t["title"], *range_times(r))
            except clockify.ClockifyError as e:
                clockify_error = "%s: %s" % (t["title"], e)
                return changed  # retried by the archiver thread
            changed, clockify_error = True, None
    return changed


def retry_logging():
    """Log ranges that failed before. The marks are written without changing the file's mtime: it is the
    board's version (open pages would get a conflict) and the archive's day."""
    try:
        st = os.stat(TASKS_FILE)
    except FileNotFoundError:
        return
    tasks = read_tasks()
    if log_pending(tasks):
        write_tasks(tasks)
        os.utime(TASKS_FILE, ns=(st.st_atime_ns, st.st_mtime_ns))


def clockify_links():
    """GEN id -> ("projectId/taskId", task name without the id) of my Clockify tasks named "GEN-<n>:...", fresh."""
    return {m.group(1): (p["id"] + "/" + t["id"], t["name"][m.end():].strip())
            for p in clockify.catalog(refresh=True) for t in p["tasks"] for m in [GEN_RE.match(t["name"])] if m}


def add_tickets(found, links):
    """Add Notion tickets that aren't on the board yet: In progress ones I'm assigned to into In progress, the rest
    into Backlog; titled by their Clockify task. Returns the ones whose Clockify task isn't there yet (not added)."""
    left, added = [], False
    with tasks_lock:
        tasks = read_tasks()
        have = {t["notion"] for t in tasks}
        for n in found:
            if n["gen"] in have:
                continue
            if clockify.enabled() and n["gen"] not in links:
                left.append(n)
                continue
            link, name = links.get(n["gen"], ("", ""))
            tasks.append({"title": name or n["title"], "prio": n["prio"] if n["prio"] in PRIOS else "Medium",
                          "status": "progress" if n["role"] == "assignee" and n["status"] == "In progress" else "backlog",
                          "clockify": link, "notion": n["gen"], "role": n["role"], "times": []})
            added = True
        if added:
            write_tasks(tasks)
    return left


def notion_sync():
    """The daily import, also run by the Sync button. Takes my Notion tickets (notion.my_tasks); for the ones already on
    the board it only fills in my role, and moves a ticket I'm assigned to from Done back to In progress when it is
    In progress in Notion again (the reviewer sent it back). New ones assigned to me that are still Backlog in Notion are
    set to Not started there, so their Clockify task gets created. A new ticket is added once its Clockify task is
    there. Returns (number added, tickets still waiting for their Clockify task). Raises NotionError / ClockifyError."""
    global notion_error
    day = date.today().isoformat()
    found = notion.my_tasks(day)
    links = clockify_links() if clockify.enabled() else {}
    gens = {link: gen for gen, (link, _) in links.items()}
    by_gen = {n["gen"]: n for n in found}
    with tasks_lock:
        tasks, back = read_tasks(), []
        for t in tasks:
            if not t["notion"] and t["clockify"] in gens:  # linked to the Clockify task of a Notion ticket by hand
                t["notion"] = gens[t["clockify"]]
            n = by_gen.get(t["notion"])
            if n:
                t["role"] = n["role"]
                if n["role"] == "assignee" and n["status"] == "In progress" and t["status"] == "done":
                    t["status"] = "progress"
                    back.append(t)
        tasks = [t for t in tasks if not any(t is b for b in back)] + back  # at the bottom of In progress
        have = {t["notion"] for t in tasks}
        write_tasks(tasks, notion_day=day)
    new = []
    for n in found:
        if n["gen"] in have:
            continue
        if n["role"] == "assignee" and n["status"] == "Backlog":
            try:
                notion.set_status(n["id"], "Not started")
            except notion.NotionError as e:
                notion_error = "%s: %s" % (n["gen"], e)
                continue
            n["status"] = "Not started"
        new.append(n)
    left = add_tickets(new, links)
    return len(new) - len(left), left


def wait_for_clockify(left):
    """Check Clockify every 10 s, 6 times, and add the tickets whose task appeared; the rest is reported."""
    global notion_error
    for _ in range(6):
        if not left:
            return
        time.sleep(10)
        try:
            left = add_tickets(left, clockify_links())
        except clockify.ClockifyError:
            pass
    if left:
        notion_error = "no Clockify task after a minute for %s, not added. Sync again later." % ", ".join(
            n["gen"] for n in left)


def run_sync():
    """Sync under sync_lock (already acquired), waiting for missing Clockify tasks; returns (added, waiting gens).
    The lock is released when the waiting is over. Raises like notion_sync."""
    global notion_error
    notion_error = None
    try:
        added, left = notion_sync()
    except Exception:
        sync_lock.release()
        raise

    def wait():
        try:
            wait_for_clockify(left)
        finally:
            sync_lock.release()

    threading.Thread(target=wait, daemon=True).start()
    return added, [n["gen"] for n in left]


def archiver():
    global notion_error
    while True:
        with tasks_lock:
            if clockify.enabled():
                retry_logging()
            archive_done()  # before the Notion sync, which changes the file's day
        if notion.enabled() and read_state().get("notion_day") != date.today().isoformat() \
                and sync_lock.acquire(blocking=False):
            try:
                run_sync()
            except (notion.NotionError, clockify.ClockifyError) as e:
                notion_error = "sync: %s" % e  # retried in a minute
        prune_archive()
        time.sleep(60)


def tasks_state():
    try:
        version = str(os.stat(TASKS_FILE).st_mtime_ns)
    except FileNotFoundError:
        version = "0"
    return {"version": version, "tasks": read_tasks(), "clockify_error": clockify_error,
            "notion": notion.enabled(), "notion_error": notion_error}


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
        """Replace the whole task list (a range without a date is dated today); 409 if the file changed since the client last read it.
        Changes of logged ranges are mirrored to Clockify first (502 and nothing saved if that fails); then newly
        finished ranges of linked tasks are logged. The reply has the tasks in the client's order."""
        if self.path != "/tasks":
            return self.send_error(404)
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with tasks_lock:
            state = tasks_state()
            if body["version"] != state["version"]:
                return self.send_json(409, state)
            tasks = [{"title": " ".join(t["title"].split()), "status": t["status"], "prio": t["prio"],
                      "clockify": t.get("clockify") or "", "notion": t.get("notion") or "", "role": t.get("role") or "",
                      "times": [[r[0], r[1], r[2] if len(r) > 2 and isinstance(r[2], str) else "",
                                 r[3] if len(r) > 3 and r[3] else date.today().isoformat()] for r in t["times"]]}
                     for t in body["tasks"]]
            merge_ids(tasks, state["tasks"])
            if clockify.enabled():
                try:
                    sync_changes(tasks, state["tasks"])
                except clockify.ClockifyError as e:
                    return self.send_json(502, dict(state, error="Clockify: %s. Change not saved." % e))
            write_tasks(tasks)  # save first: the time is kept even if Clockify is slow or down
            if clockify.enabled() and log_pending(tasks):
                write_tasks(tasks)
            self.send_json(200, dict(tasks_state(), tasks=tasks))

    def do_POST(self):
        """Notion:
        /notion/sync: run the sync now; replies {added, waiting: [GEN ids waiting for their Clockify task]}.
        /notion/move {notion: "GEN-<n>", to: backlog|progress|done}: a ticket was moved to that column. Applies the rules
          (I'm assigned: In progress when it was Backlog / Not started / Blocked, not for is_weekly ones;
          I'm the reviewer: back to Backlog = In progress when it was In review). For done nothing changes, the reply has
          the suggested status: is_weekly = "" (leave it), with a reviewer = In review, else Done; I'm the reviewer: Done.
          Replies {role, status: its Notion status now, changed, suggest}.
        /notion/status {notion, status: Done | In review}: set it."""
        if not notion.enabled():
            return self.send_json(400, {"error": "Notion is not set up"})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        gen = str(body.get("notion"))
        try:
            if self.path == "/notion/sync":
                if not sync_lock.acquire(blocking=False):
                    return self.send_json(409, {"error": "a sync is already running"})
                added, waiting = run_sync()
                return self.send_json(200, {"added": added, "waiting": waiting})
            if not re.fullmatch(r"[A-Z]+-\d+", gen):
                return self.send_json(400, {"error": "bad request"})
            if self.path == "/notion/move" and body.get("to") in ("backlog", "progress", "done"):
                n, to, status = notion.get_task(gen), body["to"], None
                if to == "progress" and n["role"] == "assignee" and not n["weekly"] and \
                        n["status"] in ("Backlog", "Not started", "Blocked"):
                    status = "In progress"
                if to == "backlog" and n["role"] == "reviewer" and n["status"] == "In review":
                    status = "In progress"
                if status:
                    notion.set_status(n["id"], status)
                suggest = "" if to != "done" or not n["role"] else "Done" if n["role"] == "reviewer" else \
                    "" if n["weekly"] else "In review" if n["reviewed"] else "Done"
                return self.send_json(200, {"role": n["role"], "status": status or n["status"], "changed": bool(status),
                                            "suggest": suggest})
            if self.path == "/notion/status" and body.get("status") in ("Done", "In review"):
                n = notion.get_task(gen)
                if n["status"] != body["status"]:
                    notion.set_status(n["id"], body["status"])
                return self.send_json(200, {"status": body["status"], "changed": n["status"] != body["status"]})
            self.send_json(400, {"error": "bad request"})
        except (notion.NotionError, clockify.ClockifyError) as e:
            self.send_json(502, {"error": str(e)})

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
        elif self.path.split("?")[0] == "/clockify":
            if not clockify.enabled():
                return self.send_json(200, {"enabled": False, "projects": []})
            try:
                self.send_json(200, {"enabled": True, "projects": clockify.catalog(refresh="refresh" in self.path)})
            except clockify.ClockifyError as e:
                self.send_json(502, {"error": str(e)})
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
    migrate_md()
    threading.Thread(target=sampler, daemon=True).start()
    threading.Thread(target=archiver, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    print(f"Dashboard on http://0.0.0.0:{PORT}  (open http://<this-pc-ip>:{PORT} on the iPad)")
    server.serve_forever()
