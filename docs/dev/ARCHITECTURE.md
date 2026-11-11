# Architecture

## In one minute

The PC runs one Python server; an iPad opens its page on the LAN. The **System** page streams the PC's stats. The **Tasks** page is a board stored in `tasks.json` that mirrors work to two services:

1. A ticket is created in **Notion** (by hand, or by *+ Create* on the board).
2. An **external pipeline** (not in this repo) creates a **Clockify** task for it named `GEN-<n>:<milestone> - <task>`, once its Notion status is *Not started* (it left Backlog; the board sets this itself for Backlog tickets it imports, adds or creates).
3. The ticket comes to the board: the daily sync / ⟳ Notion (due today or earlier), or *+ Add*. The `GEN-<n>` prefix links board ticket ↔ Clockify task ↔ Notion ticket.
4. You run its timer; each finished time range becomes a Clockify **time entry**.
5. Moving the ticket on the board sets its Notion **status** (In progress; on Done you are asked: Done / In review / leave; full rules in [Board moves](#notion-sync)).
6. The next day its Done ticket is archived to `archive/YYYY-MM-DD.jsonl`.

**Security:** the server listens on all interfaces (`0.0.0.0:8765`) with **no login**; anyone on the LAN can change the board, Clockify entries and Notion statuses. Keep it on a trusted home network; never expose it to the internet.

## Glossary

| Term | Meaning |
|---|---|
| ticket | an item on the board (a `tasks` entry in `tasks.json`) |
| Notion ticket / Task ID | a row of the Notion **Tasks** database (General-Task), identified by its Task ID `GEN-<n>` (the `notion` field of a ticket) |
| Notion project | a row of the Notion **Projects** database (General-Projects); its *Owner* is the only one who creates tickets in it |
| Clockify task | the Clockify task of a Notion ticket, named `GEN-<n>:…` (the `clockify` field: `projectId/taskId`) |
| time entry | a logged time range in Clockify |
| role | my role on a Notion ticket: `assignee` or `reviewer` |
| milestone | a ticket's Parent-task (any level); the Clockify task name shows it before the ticket's own name |
| weekly ticket | Notion checkbox `is_weekly`: a recurring ticket that stays In progress; board moves don't set it In progress and the Done dialog suggests *Leave as is* |
| personal ticket | a ticket with no `notion` and no `clockify`: tracked on the board only |
| pick list (`picks`) | cached list of my Notion tickets for *+ Add*; `projects` is the cached project trees for *+ Create* |
| data source | Notion API name for a database's table (`notion.TASKS`, `notion.PROJECTS`) |

## Notion statuses

Notion *Backlog* is not the board's Backlog column.

| Notion status | Meaning | On the board (import) |
|---|---|---|
| Backlog | not groomed, no Clockify task yet | assigned: set to Not started, added to Backlog once its Clockify task exists |
| Not started | ready; the pipeline creates the Clockify task | Backlog |
| In progress | the assignee works on it | assigned: In progress; reviewer: not imported (it is back with the assignee) |
| In review | waiting for the reviewer | reviewer: Backlog; assigned: not imported |
| Blocked | waiting on someone else | not imported |
| Done, Archived | closed | not imported |

The board changes Notion status only by the [board-move rules](#notion-sync) and the Done dialog.

## Stack

- **Server:** one Python process (`server.py`), standard library `http.server` (`ThreadingHTTPServer`), plus `psutil` (system stats) and `pyyaml` (`config.yaml`). Dependencies are managed by uv (`pyproject.toml`, `uv.lock`).
- **API clients:** `clockify.py` and `notion.py`, plain `urllib` REST clients (no SDKs). They read their keys from the environment (`.env` is loaded by `server.py` at start; real environment variables win).
- **Frontend:** a single `index.html` with inline CSS and vanilla JavaScript (no build step, no libraries), written for iPad Safari. The System page reads `/stream` (only while shown). The board keeps the whole task list in memory and saves it whole with `PUT /tasks` on every change; it polls `GET /tasks` every 10 s; a **409** reply means the file changed elsewhere, so it reloads. Notion actions go to `/notion/*`.
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
| `notion.py` | Notion: my tickets for the board (`my_tasks`), my tickets to add by hand with project and milestone names (`pickable`), my projects and their ticket trees (`my_projects`), `create_task`, one ticket by Task ID (`get_task`), `set_status`, PR links (`pr_urls`) |
| `index.html` | both pages, the board, drag & drop, the ticket sheet, the + Add / + Create dialog, the Done dialog |
| `config.yaml` | `archive_retention_days` |
| `.env` | API keys (not committed; `.env.sample` is the template) |
| `tasks.json` | the board (not committed; `TASKS_FILE` overrides the path) |
| `archive/YYYY-MM-DD.jsonl` | archived Done tickets, one JSON object per line: `{date, title, times}` |

## Threads

| Thread | Does |
|---|---|
| `sampler` | every second: reads system stats into `latest` and wakes the `/stream` clients |
| `archiver` | every minute: loads the pick lists if they are missing, retries failed Clockify logging, archives the Done tickets when the task file was last written before today (into that day's archive file), starts the daily Notion sync when `notion_day` isn't today, prunes old archive files |
| request threads | one per HTTP request |
| link waiter | after adding (+ Add) or creating (+ Create) a Notion ticket without its Clockify task yet: polls Clockify (10 s × 6) and links it |
| sync waiter | after a Notion sync: reloads the pick list (`load_picks`), then polls Clockify for tickets still missing their Clockify task (10 s × 6), then releases `sync_lock` |

`tasks_lock` serialises every read-modify-write of `tasks.json`. `sync_lock` allows one Notion sync at a time (the daily one and the button); it is held until the waiter is done.

## HTTP API

| Method, path | Body | Reply |
|---|---|---|
| `GET /` | | `index.html` |
| `GET /stream` | | Server-Sent Events, one stats JSON per second |
| `GET /tasks` | | `{version, tasks, clockify_error, notion, notion_error}` |
| `PUT /tasks` | `{version, tasks}` | the saved state; **409** + current state if `version` is stale; **502** if a Clockify edit/delete failed (nothing saved) |
| `GET /clockify[?refresh]` | | `{enabled, projects: [{id, name, client, tasks: [{id, name, billable}]}]}` |
| `GET /notion/tickets` | | `{tickets: [{gen, label, project, status, role, …}], projects: [{id, name, tickets: [{id, gen, title, parent}]}], error}` — the cached pick list and the projects I own with their open ticket trees |
| `POST /notion/create` | `{title, prio, project, parent, due}` | the task state — creates the Notion ticket under `parent` ("" = top level) |
| `GET /notion/people?GEN-<n>` | | `{assignees, reviewers, status, prs}` — names and GitHub PR URLs, for the ticket sheet |
| `POST /notion/add` | `{title, notion}` | the task state; **409** if that Notion ticket is already on the board |
| `POST /notion/sync` | | `{added, waiting: [GEN ids]}`; **409** while a sync runs |
| `POST /notion/move` | `{notion, to: backlog\|progress\|done}` | `{role, status, changed, suggest}` |
| `POST /notion/status` | `{notion, status: Done\|In review}` | `{status, changed}` |

Errors: `/notion/*` replies **400** `{error}` when Notion isn't set up or the request is invalid, **502** `{error}` when Notion or Clockify fails (`GET /notion/tickets` also when the pick list can't be loaded). `GET /tasks` carries the last background errors for the board to show: `clockify_error` (time entry logging, retried every minute) and `notion_error` (sync, waiting for a Clockify task).

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

On `PUT /tasks` the server compares the new list with the saved one: removed logged ranges delete their entry, changed ones (time, date, title, link) update it (`sync_changes`); if that fails, nothing is saved (502). Then the file is written, and finished (end time set, not 0 minutes), unlogged ranges of tickets with a `clockify` link are logged (`log_pending`); personal tickets never are. Failed logging is retried every minute by the archiver.

## Notion sync

**Linking.** Our Clockify tasks are created from Notion by a separate pipeline (outside this repo; this app depends on its contract: the name format below, a task created for the assignee when the ticket leaves Backlog, the reviewer added at In review, closed at Done) and named `<Task ID>:<milestone> - <task>`. `GEN_RE` reads the Task ID from the name; that is the only link between a board ticket, its Clockify task and its Notion ticket. The pipeline creates the Clockify task when the Notion ticket leaves Backlog (for the assignee), adds the reviewer when it is In review, and closes it when it is Done.

**Daily import / ⟳ Notion button** (`run_sync` → `notion_sync`, then `wait_for_clockify`):

1. `notion.my_tasks(today)` — one query on the Tasks data source: due ≤ today and (assignee = me and status not Blocked / In review / Done / Archived) or (reviewer = me and status = In review).
2. Load my Clockify tasks fresh and map Task ID → Clockify task.
3. Board tickets that aren't in the result and don't know my role yet (e.g. no due date) get it from `get_task`, one call each.
4. Under `tasks_lock`: board tickets linked to a `GEN-…` Clockify task get `notion`; tickets get `role`; an assigned ticket in Done that is In progress in Notion moves back to In progress. `notion_day` is written.
5. New tickets (not on the board): assigned + Backlog → `set_status(Not started)`. Each is added (`add_tickets`) if its Clockify task exists, else it waits: Clockify is polled every 10 s, 6 times, then `notion_error` lists the ones still missing.

**Adding by hand** (`POST /notion/add`). The pick list (`picks`, from `notion.pickable`: assigned and not Blocked / Done / Archived, or In review where I review, plus project and milestone names) is loaded by every sync and at start, so the picker opens without Notion calls. The added ticket keeps the typed title, gets the Notion priority, goes to In progress if assigned + In progress, else Backlog; assigned + Backlog → `set_status(Not started)`. Without its Clockify task yet, `link_when_ready` links it within a minute; any sync also links board tickets that have `notion` but no `clockify`.

**Creating** (`POST /notion/create`). `projects` (from `notion.my_projects`: In progress projects whose Owner is me, with all their open tickets and each one's parent — the tree can be any depth) is cached with `picks`. `notion.create_task` creates the page in the Tasks data source (assignee me, status Not started, priority, project, parent, due, a `## Description` heading); the board ticket goes to Backlog and `link_when_ready` links its Clockify task. The new ticket is added to the cached tree at once.

**Personal tickets** (no `notion`, no `clockify`) are only on the board: the sheet hides the Clockify picker for them.

**Board moves.** The browser calls `POST /notion/move` on every column change of a ticket with `notion`. The server fetches the ticket (`get_task`: status, my role, has a reviewer, `is_weekly`) and applies:

| Role | To | Rule |
|---|---|---|
| assignee | progress | Backlog / Not started / Blocked → In progress, unless `is_weekly` |
| reviewer | backlog | In review → In progress |
| any | done | nothing changes; `suggest` = reviewer: Done; assignee: `is_weekly` → "" (leave), has reviewer → In review, else Done; no role → "" |

For Done the browser shows the dialog with `suggest` highlighted and sends the choice to `POST /notion/status`. The reply's `role` is stored on the ticket (the browser saves it), which is how hand-linked tickets learn their role.
