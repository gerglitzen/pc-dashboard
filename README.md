# PC Dashboard

A live system monitor for this PC that you open in a web browser on another device on the same network (e.g. an old iPad), so that device works as a wireless status screen.

It shows CPU (total, each core, clock, load), CPU temperature, fan speed, SSD temperature, RAM and swap, network download/upload, GPU (NVIDIA), disk read/write and space used, battery, and uptime. Values update every second and the charts cover the last 2 minutes. Tap the CPU or Memory tile to see the top 5 processes (processes with the same name are added together, e.g. all `chrome` processes).

## Tasks page

Swipe left (or tap **Tasks** in the header) for a small task board: Backlog (High / Medium / Low), In progress, Done. Type in *Add a task* and press Enter to add one. Press and hold a ticket to drag it to another column, priority or position (with a mouse just drag it). The ▶ button on In progress tickets starts/stops their timer. Tap a ticket to move it, rename it, delete it, start/stop its timer or add a time range by hand. Only one timer runs at a time; starting one moves the ticket to In progress, moving a ticket out of In progress stops its timer.

Tasks are stored in a plain markdown file that you can also edit by hand:

```
# Backlog
## High
[ ] Some task
## Medium
## Low

# In progress
[ ] Working on this (13:05-)

# Done
[x] Finished task (9:00-10:00; 10:50-11:00)
```

The file is `tasks.md` next to `server.py` by default; set `TASKS_FILE=/path/to/tasks.md` to use another one (for the systemd service add `Environment=TASKS_FILE=...` under `[Service]`). If the file is changed on the PC while the iPad has the page open, the iPad reloads it instead of overwriting your edit.

## Requirements

- Linux with Python 3 and `psutil` (`sudo apt install python3-psutil` or `pip install psutil`)
- Optional: `nvidia-smi` for GPU stats (the GPU tile shows "not available" without it)

## Run manually

```bash
python3 server.py            # listens on port 8765
PORT=9000 python3 server.py  # use a different port
```

Open `http://<pc-ip>:8765` on the iPad. Find the PC's IP with `hostname -I`.

If the page doesn't load, open the port in the firewall:

```bash
sudo ufw allow 8765/tcp
```

## iPad tips

- **Full screen:** in Safari tap Share → *Add to Home Screen*, then open it from that icon.
- **Theme:** the button in the top-right corner switches between dark and light; the choice is remembered in that browser.
- **Keep the screen on:** Settings → Display & Brightness → Auto-Lock → *Never*.

## Start automatically (systemd user service)

### Add

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/pc-dashboard.service <<EOF
[Unit]
Description=PC Dashboard (LAN system monitor)
After=network-online.target

[Service]
ExecStart=/usr/bin/python3 $(pwd)/server.py
Restart=on-failure
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now pc-dashboard
```

Run this from the project folder: `$(pwd)` fills in the path to `server.py`.

This starts the dashboard when you log in. To start it at boot, before anyone logs in:

```bash
sudo loginctl enable-linger $USER
```

### Check

```bash
systemctl --user status pc-dashboard
journalctl --user -u pc-dashboard -f     # logs
systemctl --user restart pc-dashboard    # after editing server.py
```

### Remove

```bash
systemctl --user disable --now pc-dashboard
rm ~/.config/systemd/user/pc-dashboard.service
systemctl --user daemon-reload
sudo loginctl disable-linger $USER       # only if you enabled linger
```
