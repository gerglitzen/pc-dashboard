# Architecture

## Stack

- **Server:** one Python process (`server.py`), standard library `http.server` (`ThreadingHTTPServer`), plus `psutil` (system stats) and `pyyaml` (`config.yaml`). Dependencies are managed by uv (`pyproject.toml`, `uv.lock`).
- **API clients:** `clockify.py` and `notion.py`, plain `urllib` REST clients (no SDKs). They read their keys from the environment (`.env` is loaded by `server.py` at start; real environment variables win).
- **Frontend:** a single `index.html` with inline CSS and vanilla JavaScript (no build step, no libraries), written for iPad Safari.
- **Storage:** JSON / JSONL files next to the server. No database.

```
iPad browser ──HTTP──▶ server.py ──▶ tasks.json, archive/*.jsonl
                         │
                         ├──▶ clockify.py ──▶ Clockify REST API (time entries, my tasks)
                         └──▶ notion.py   ──▶ Notion REST API (Tasks data source, version 2025-09-03)
```

## Files

| File | Role |
|---|---|
| `server.py` | HTTP server, stats sampler, task storage, archive, Clockify logging, Notion sync |
| `clockify.py` | Clockify: my open tasks per project (cached; `refresh=True` reloads), create / update / delete time entries |
| `notion.py` | Notion: my tickets for the board (`my_tasks`), one ticket by Task ID (`get_task`), `set_status` |
| `index.html` | both pages, the board, drag & drop, the ticket sheet, the Done dialog |
| `config.yaml` | `archive_retention_days` |
| `.env` | API keys (not committed; `.env.sample` is the template) |
| `tasks.json` | the board (not committed; `TASKS_FILE` overrides the path) |
| `archive/YYYY-MM-DD.jsonl` | archived Done tickets, one JSON object per line: `{date, title, times}` |

## Threads

| Thread | Does |
|---|---|
| `sampler` | every second: reads system stats into `latest` and wakes the `/stream` clients |
| `archiver` | every minute: retries failed Clockify logging, archives yesterday's Done tickets, starts the daily Notion sync when `notion_day` isn't today, prunes old archive files |
| request threads | one per HTTP request |
| sync waiter | after a Notion sync: polls Clockify for tickets still missing their Clockify task (10 s × 6), then releases `sync_lock` |

`tasks_lock` serialises every read-modify-write of `tasks.json`. `sync_lock` allows one Notion sync at a time (the daily one and the button); it is held until the waiter is done.

## HTTP API

| Method, path | Body | Reply |
|---|---|---|
| `GET /` | | `index.html` |
| `GET /stream` | | Server-Sent Events, one stats JSON per second |
| `GET /tasks` | | `{version, tasks, clockify_error, notion, notion_error}` |
| `PUT /tasks` | `{version, tasks}` | the saved state; **409** + current state if `version` is stale; **502** if a Clockify edit/delete failed (nothing saved) |
| `GET /clockify[?refresh]` | | `{enabled, projects: [{id, name, client, tasks: [{id, name, billable}]}]}` |
| `POST /notion/sync` | | `{added, waiting: [GEN ids]}`; **409** while a sync runs |
| `POST /notion/move` | `{notion, to: backlog\|progress\|done}` | `{role, status, changed, suggest}` |
| `POST /notion/status` | `{notion, status: Done\|In review}` | `{status, changed}` |

`version` is the task file's modification time in ns: every write by anyone (the board, the archive, the Notion sync) changes it, so a client that saves on an old version gets 409 and reloads. Writes that only add Clockify entry ids (`retry_logging`) keep the old mtime and are merged back by `merge_ids` instead.

## Task file

```json
{
 "notion_day": "2026-11-10",
 "tasks": [
  {"title": "MLOps Feature - Meeting 2026-11-10", "status": "progress", "prio": "High",
   "clockify": "<projectId>/<taskId>", "notion": "GEN-4544", "role": "assignee",
   "times": [["9:00", "10:30", "<timeEntryId>", "2026-11-10"], ["11:00", "", "", "2026-11-10"]]}
 ]
}
```

- `tasks` is in board order; the board groups Backlog and In progress by `prio`.
- `status`: `backlog` | `progress` | `done`. `prio`: `High` | `Medium` | `Low`.
- `times`: `[start, end, Clockify entry id, day the range started]`; empty end = timer running; empty id = not logged.
- `notion`: the Notion Task ID; `role`: my role on it (`assignee` | `reviewer` | `""` = not known yet / none).
- `notion_day`: the day of the last successful Notion sync (the daily trigger).
- An old `tasks.md` (the earlier markdown format) is converted once on start and renamed to `tasks.md.bak` (`migrate_md`).

## Clockify logging

On `PUT /tasks` the server compares the new list with the saved one: removed logged ranges delete their entry, changed ones (time, date, title, link) update it (`sync_changes`); if that fails, nothing is saved (502). Then the file is written, and finished, unlogged ranges of linked tickets are logged (`log_pending`). Failed logging is retried every minute by the archiver.

## Notion sync

**Linking.** Our Clockify tasks are created from Notion by a separate pipeline and named `<Task ID>:<milestone> - <task>`. `GEN_RE` reads the Task ID from the name; that is the only link between a board ticket, its Clockify task and its Notion ticket. The pipeline creates the Clockify task when the Notion ticket leaves Backlog (for the assignee), adds the reviewer when it is In review, and closes it when it is Done.

**Daily import / Sync button** (`run_sync` → `notion_sync`, then `wait_for_clockify`):

1. `notion.my_tasks(today)` — one query on the Tasks data source: due ≤ today and (assignee = me and status not Blocked / In review / Done / Archived) or (reviewer = me and status = In review).
2. Load my Clockify tasks fresh and map Task ID → Clockify task.
3. Board tickets that aren't in the result and don't know my role yet (e.g. no due date) get it from `get_task`, one call each.
4. Under `tasks_lock`: board tickets linked to a `GEN-…` Clockify task get `notion`; tickets get `role`; an assigned ticket in Done that is In progress in Notion moves back to In progress. `notion_day` is written.
5. New tickets (not on the board): assigned + Backlog → `set_status(Not started)`. Each is added (`add_tickets`) if its Clockify task exists, else it waits: Clockify is polled every 10 s, 6 times, then `notion_error` lists the ones still missing.

**Board moves.** The browser calls `POST /notion/move` on every column change of a ticket with `notion`. The server fetches the ticket (`get_task`: status, my role, has a reviewer, `is_weekly`) and applies:

| Role | To | Rule |
|---|---|---|
| assignee | progress | Backlog / Not started / Blocked → In progress, unless `is_weekly` |
| reviewer | backlog | In review → In progress |
| any | done | nothing changes; `suggest` = reviewer: Done; assignee: `is_weekly` → "" (leave), has reviewer → In review, else Done; no role → "" |

For Done the browser shows the dialog with `suggest` highlighted and sends the choice to `POST /notion/status`. The reply's `role` is stored on the ticket (the browser saves it), which is how hand-linked tickets learn their role.
