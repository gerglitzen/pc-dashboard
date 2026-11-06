# PC Dashboard

A live system monitor for this PC that you open in a web browser on another device on the same network (e.g. an old iPad), so that device works as a wireless status screen.

It shows CPU (total, each core, clock, load), CPU temperature, fan speed, SSD temperature, RAM and swap, network download/upload, GPU (NVIDIA), disk read/write and space used, battery, and uptime. Values update every second and the charts cover the last 2 minutes. Tap the CPU or Memory tile to see the top 5 processes (processes with the same name are added together, e.g. all `chrome` processes).

## Tasks page

Swipe left (or tap **Tasks** in the header) for a small task board: Backlog and In progress (both grouped High / Medium / Low), Done. Type in *Add a task*, press Enter, then pick its priority (High / Medium / Low). Tickets have a colored stripe for their priority (red / yellow / gray). Press and hold a ticket to drag it to another column, priority or position (with a mouse just drag it). The ▶ button on In progress tickets starts/stops their timer. Tap a ticket to move it, change its priority (it stays in its column), rename it, delete it, start/stop its timer or add a time range by hand. Only one timer runs at a time; starting one moves the ticket to In progress, moving a ticket out of In progress stops its timer.

Tasks are stored in a plain markdown file that you can also edit by hand:

```
# Backlog
## High
[ ] Some task
## Medium
## Low

# In progress
## High
[ ] Working on this (13:05-)
## Medium
## Low

# Done
[x] Finished task !Low (9:00-10:00; 10:50-11:00)
```

Done tickets keep their priority as a `!High` / `!Medium` / `!Low` tag (no tag = Medium).

The file is `tasks.md` next to `server.py` by default; set `TASKS_FILE=/path/to/tasks.md` to use another one (for the systemd service add `Environment=TASKS_FILE=...` under `[Service]`). If the file is changed on the PC while the iPad has the page open, the iPad reloads it instead of overwriting your edit.

On a new day, Done tasks are moved out of `tasks.md` into `archive/YYYY-MM-DD.jsonl` (next to the task file, one JSON object per line: `{"date", "title", "times"}`), so the board starts each day with an empty Done column. The date is the day the task file was last changed. Archive files older than 30 days are deleted; change this in `config.yaml`:

```yaml
archive_retention_days: 30
```

## Clockify

A ticket can be linked to a Clockify task: tap the ticket, pick the project and the task under **Clockify**. The list shows the open tasks of active projects that you can log time to (assigned to you, to one of your groups, or to anyone). Every time range you finish on a linked ticket (stopping its timer or *Add time*) is logged to Clockify as a time entry for today, with the ticket title as description. Logged ranges get a ✓ in the ticket; if logging fails, the error is shown and the server retries every minute.

Logged ranges stay in sync with Clockify:

- **Edit** a range: tap its times, change them, *Confirm* → its entry is updated (same day).
- **Del** on a range, or deleting the ticket → its entries are deleted.
- Renaming the ticket or linking it to another task → its entries are updated.
- Unlinking the ticket (*Not linked*) only stops logging new ranges; existing entries stay.
- If Clockify can't be reached for an edit or delete, the change is not saved and the board shows why.
- Ranges that were already on the ticket when you link it are logged too, dated today (you are asked first).

Setup: copy `.env.sample` to `.env` next to `server.py` and fill in `CLOCKIFY_API_KEY` and `CLOCKIFY_USER_ID`, then restart the server. Without them the Clockify section is hidden. The project/task list is cached; restart the server or open `http://<pc-ip>:8765/clockify?refresh` to reload it.

In `tasks.md` the link and the logged marks look like this:

```
[ ] Planning {cf:<projectId>/<taskId>} (9:00-10:00✓<timeEntryId>; 10:50-)
```

## Requirements

- Linux with [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`). Dependencies (`psutil`, `pyyaml`) are listed in `pyproject.toml`; `uv run` installs them into `.venv` on first start
- Optional: `nvidia-smi` for GPU stats (the GPU tile shows "not available" without it)

## Run manually

```bash
uv run server.py            # listens on port 8765
PORT=9000 uv run server.py  # use a different port
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
ExecStart=$(which uv) run --project $(pwd) $(pwd)/server.py
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
