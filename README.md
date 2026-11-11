# PC Dashboard

A live system monitor for this PC that you open in a browser on another device on the same network (e.g. an old iPad), plus a small task board that logs time to Clockify and follows your Notion tickets.

- **System** page: CPU, temperatures, fans, RAM, network, GPU, disk, battery — updated every second.
- **Tasks** page: Backlog / In progress / Done board with timers; linked to Clockify (time entries) and Notion (tickets, statuses).

## Which document to read

| You want to… | Read |
|---|---|
| Install and start it | this README (below) |
| Use the app | [User guide](docs/userguide/USER_GUIDE.md) → [System page](docs/userguide/SYSTEM.md), [Tasks page](docs/userguide/TASKS.md) |
| Understand how it works | [Architecture](docs/dev/ARCHITECTURE.md) |
| Change the code | [Development](docs/dev/DEVELOPMENT.md) |

## Setup

Requirements: Linux with [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`). Optional: `nvidia-smi` for GPU stats.

1. **Configure** (optional — without it the board works on its own):

   ```bash
   cp .env.sample .env    # then fill in the values
   ```

   | Variable | What | Without it |
   |---|---|---|
   | `CLOCKIFY_API_KEY`, `CLOCKIFY_USER_ID` | Clockify → Profile settings → API → Generate; your user id: `curl -s -H "X-Api-Key: <key>" https://api.clockify.me/api/v1/user` → `id` | no Clockify section, no time entries |
   | `NOTION_API_KEY`, `NOTION_USER_ID` | a Notion internal integration token (notion.so/my-integrations); your Notion user id | no Notion sync |

   For Notion, connect the integration to the **Tasks** and **Projects** databases (database ⋯ → Connections), and to the GitHub Pull Requests database if you want PR links. The app uses these Tasks properties: Task Name, Task ID, Status, Assignee, Reviewer, Due, Priority, Project, Parent-task, is_weekly, GitHub Pull Requests; and Project Name, Owner, Status of Projects. Find your user id with `curl -s -H "Authorization: Bearer <token>" -H "Notion-Version: 2025-09-03" https://api.notion.com/v1/users` (your entry's `id`).

   `config.yaml` sets how long archived Done tasks are kept (`archive_retention_days`, default 30).

2. **Run:**

   ```bash
   uv run server.py            # port 8765; PORT=9000 uv run server.py for another one
   ```

   Open `http://<pc-ip>:8765` on the iPad (`hostname -I` shows the PC's IP). If it doesn't load: `sudo ufw allow 8765/tcp`.

3. **Start automatically** (systemd user service; run from the project folder):

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
   sudo loginctl enable-linger $USER   # optional: start at boot, before anyone logs in
   ```

   Restart it after changing code or `.env`: `systemctl --user restart pc-dashboard`. Logs, status and removing the service: see [Development](docs/dev/DEVELOPMENT.md#the-systemd-service).

## iPad tips

- **Full screen:** in Safari tap Share → *Add to Home Screen*, then open it from that icon.
- **Keep the screen on:** Settings → Display & Brightness → Auto-Lock → *Never*.
