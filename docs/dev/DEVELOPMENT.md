# Development

How the code fits together is in [Architecture](ARCHITECTURE.md); setup is in the [README](../../README.md).

## Run a development copy

The systemd service runs from the project folder, so keep a separate port and task file while you work:

```bash
TASKS_FILE=/tmp/dev/tasks.json PORT=8799 uv run server.py
```

- `TASKS_FILE` points the board (and its `archive/` folder) somewhere else, so your real board is untouched.
- The `.env` keys are still used. **The Clockify and Notion calls are real:** finished time ranges on linked tickets become real time entries, the daily Notion sync can set tickets to *Not started*, and board moves can change Notion statuses. Leave out keys you don't need, e.g. `NOTION_API_KEY= uv run server.py` (real environment variables win over `.env`).
- `index.html` is read on every request, so frontend changes only need a browser reload; Python changes need a restart.

## Testing

There is no test suite. Test changes with small scripts that import `server` and replace the API calls, so nothing reaches Clockify or Notion:

```python
# TASKS_FILE=/tmp/t/tasks.json PYTHONPATH=. uv run python test.py
import server, notion, clockify
notion.my_tasks = lambda day: [{"id": "p1", "gen": "GEN-1", "title": "x", "status": "Backlog", "prio": "High",
                                "role": "assignee", "reviewed": False, "weekly": False}]
notion.set_status = lambda page_id, status: print("set", page_id, status)
clockify.enabled = lambda: False
server.sync_lock.acquire()
print(server.run_sync())
print(server.read_tasks())
```

For the HTTP handlers, start `server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)` in a thread and call it with `urllib`. Read-only calls against the real APIs (`notion.my_tasks`, `notion.get_task`, `clockify.catalog`) are fine for checking real data.

Check the JavaScript syntax after editing `index.html`:

```bash
python3 -c "import re;print(re.search(r'<script>(.*)</script>',open('index.html').read(),re.S).group(1))" > /tmp/x.js && node --check /tmp/x.js
```

Then try it on the iPad (or a narrow browser window): drag & drop, the ticket sheet and the Done dialog.

## Conventions

- Keep it small: standard library on the server, no frontend libraries or build step.
- The browser sends the whole task list on every change (`PUT /tasks`); the server is the one that talks to Clockify and Notion.
- Every write to `tasks.json` goes through `write_tasks` under `tasks_lock` (temp file + `os.replace`).
- Notion property names and status names are used as they are in the Tasks database (`Status`, `Assignee`, `Reviewer`, `Due`, `Priority`, `Task ID`, `is_weekly`). If the database changes, update `notion.py`.
- Update the [user guide](../userguide/USER_GUIDE.md) and [Architecture](ARCHITECTURE.md) with behaviour changes.

## The systemd service

```bash
systemctl --user status pc-dashboard
journalctl --user -u pc-dashboard -f     # logs
systemctl --user restart pc-dashboard    # after editing server.py or .env
```

Remove it:

```bash
systemctl --user disable --now pc-dashboard
rm ~/.config/systemd/user/pc-dashboard.service
systemctl --user daemon-reload
sudo loginctl disable-linger $USER       # only if you enabled linger
```
