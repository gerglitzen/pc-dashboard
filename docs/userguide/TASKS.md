# Tasks page

A small task board with three columns: **Backlog** and **In progress** (both grouped High / Medium / Low) and **Done**. Tickets can log their time to [Clockify](#clockify) and follow your [Notion](#notion) tickets.

## The board

- **Add a ticket** with the two buttons above the Backlog column (new tickets go to Backlog, except an *+ Add*-ed ticket that is already In progress in Notion):
  - **+ Add** — for one of your existing Notion tickets, see [Adding an existing Notion ticket](#adding-an-existing-notion-ticket-add).
  - **+ Create** — creates a new Notion ticket, see [Creating a Notion ticket](#creating-a-notion-ticket-create). *Skip Notion (personal)* in the same dialog adds a **personal** ticket instead.
- **Personal tickets** have no Notion ticket and are never logged to Clockify: the card shows *(PERSONAL)* after the title and *Not logged to Clockify* under it. The timer and time ranges work as usual, only on the board; the sheet has no Clockify section for them.
- **Priority** is the colored stripe: red High, yellow Medium, gray Low.
- **Move a ticket:** press and hold it, then drag it to another column, priority or position (with a mouse just drag it). Or tap it and use *Move to*.
- **Tap a ticket** to open its sheet: move it, change its priority (it stays in its column), rename it, delete it, start/stop its timer, add or edit time ranges, link it to Clockify.
  For a Notion ticket the sheet also shows, top right, who is the **Assignee** and the **Reviewer**, and links to its GitHub pull requests (*PR #347*) if it has any (loaded from Notion each time you open it).

### Time tracking

A **time range** is one start–stop span: from starting to stopping the timer, or one *Add time* entry.

- The ▶ button on In progress tickets starts / stops their timer. Only one timer runs at a time; starting one moves its ticket to In progress, moving a ticket out of In progress stops its timer.
- Every time range belongs to the day it started; a timer running past midnight stays one range on the day it started. The board shows only **today's** ranges and time (plus a running timer), so a ticket still In progress from yesterday starts the day at 0.
- The sheet shows today's ranges with today's and the total time. *Show all* lists every range grouped by day; older ranges can be edited or deleted too. *Add time* adds a range for today. Tap a range's times to edit it, *Confirm* saves it, *Del* deletes it.

### Every new day

Within a minute after midnight (or when the server starts, if it was off), on the PC's clock:

- Done tickets are moved to a local history file (`archive/YYYY-MM-DD.jsonl` on the PC — not Notion's *Archived* status), so Done starts empty. Archive files older than 30 days are deleted (`config.yaml`: `archive_retention_days`).
- Your Notion tickets are brought in, see [Notion](#notion).

If the board was changed on the PC (by the daily sync, for example) while you edit it on the iPad, your change is not saved: the board reloads and asks you to redo it.

## Clockify

Link a ticket to a Clockify task in its sheet: pick the project and the task under **Clockify**. The task must already exist in Clockify (ours are created from Notion, see below); the list shows the tasks assigned to you that aren't done, in projects that aren't archived, and is reloaded each time you open a ticket.

Every time range you finish on a linked ticket (stopping its timer or *Add time*) is logged to Clockify as a time entry on that range's date, with the ticket title as description. Logged ranges show *✓ Clockify*. If logging fails, the error is shown and the server retries every minute.

Logged ranges stay in sync:

- **Edit** a range → its entry is updated. **Del** a range or delete the ticket → its entries are deleted.
- Renaming the ticket or linking it to another task → its entries are updated.
- *Not linked* only stops logging new ranges; existing entries stay.
- Ranges already on a ticket when you link it are logged too, on their own dates (you are asked first).
- If Clockify can't be reached for an edit or delete, the change is not saved and the board tells you why.

## Notion

Your tickets in the Notion Tasks database come to the board by themselves, and moving them on the board updates their Notion status.

Terms used below:

- **Task ID** — the Notion ticket's number, e.g. `GEN-4544`. Our Clockify tasks are created from Notion and their name starts with it: `GEN-4544:MLOps Feature - Meeting 2026-11-10`. That is how a board ticket finds its Notion ticket; tickets you link to such a Clockify task by hand are Notion tickets too.
- **Project code** — the number at the start of a project's name: `2579` in `2579_OE_AI_IP`.
- **Milestone** — a ticket that groups other tickets (its sub-tickets) in a project; tickets can nest several levels deep.
- **Weekly ticket** — a recurring ticket (Notion checkbox *is_weekly*, e.g. a weekly sync) that always stays In progress; you only log time on it.

Imported tickets are titled with their Notion *Task Name*. The card shows the project after the title, without its code (`2579_OE_AI_IP` → `(OE_AI_IP)`), and the full Clockify task name below without the `GEN-` prefix (`4544:MLOps Feature - Meeting 2026-11-10`), with **Review ·** in front when you are the reviewer:

```
Deploy to OE Azure (OE_AI_IP)
4502:Common Development - Deploy to OE Azure
```

### Getting tickets: daily sync and the ⟳ Notion button

Once a day (the first minute after midnight the server runs, or when it starts), and whenever you tap **⟳ Notion** in the header, the board takes your Notion tickets that are **due today or earlier**. A ticket that is already on the board is not added again; the sync never removes tickets from the board (e.g. when their due date was moved).

**Deleting** a Notion ticket's card (sheet → *Delete*) deletes its Clockify time entries but doesn't change Notion; if it still matches the table below, the next sync adds it again.

**After it is on the board** a card follows Notion only in two ways: your role (assignee / reviewer) is kept up to date, and the *sent back* case below. Title, priority and due date changes in Notion don't reach the card; change them on the board too if needed.

| My role | Notion status | On the board |
|---|---|---|
| Assignee | Backlog | Notion is set to **Not started**, so its Clockify task gets created; the ticket is added to **Backlog** as soon as the Clockify task appears |
| Assignee | Not started | **Backlog** |
| Assignee | In progress | **In progress** |
| Assignee | Blocked, In review, Done, Archived | not added |
| Reviewer | In review | **Backlog** |

A ticket is only added together with its Clockify task. If it is missing, the board checks Clockify every 10 seconds for a minute; if it still isn't there, an error names the ticket — tap ⟳ Notion later to try again.

**Sent back by the reviewer:** a ticket you are assigned to that sits in Done on your board but is *In progress* in Notion again moves back to **In progress** at the next sync.

### Adding an existing Notion ticket (+ Add)

For tickets the daily sync doesn't bring (e.g. no due date yet): tap **+ Add**, type a name, pick the **project** and the **ticket**, tap *Add*.

- The name you typed is the ticket's title, so it is also the description of its Clockify time entries.
- The list shows your tickets that aren't on the board yet: assigned to you and *Backlog*, *Not started*, *In progress* or *In review*; and *In review* where you are the reviewer. They are named like their Clockify task (`4544:<milestone> - <task>`), with their Notion status.
- The ticket gets its priority from Notion (Medium if not set) and goes to **In progress** if it is assigned to you and In progress in Notion, otherwise to **Backlog**.
- A *Backlog* ticket assigned to you is set to **Not started** in Notion, and the board ticket is linked to its Clockify task once that appears (within a minute; otherwise at the next sync). Until then the card shows the Task ID.
- The list is loaded by the daily sync (or when the server starts) and reloaded by **⟳ Notion**, so a ticket created in Notion during the day shows up after ⟳ Notion (or the next daily sync).

### Creating a Notion ticket (+ Create)

Tap **+ Create**, type a name, pick the priority, the **project** and **where** in its ticket tree, check the due date, tap *Create*.

- **Projects:** only the *In progress* projects where you are the **Owner** (the Owner field of the project in Notion's Projects database); only the owner creates tickets in a project.
- **Where:** one list per level of the project's tree (open tickets only). Each list starts with *New ticket (top level)* on the first level and *New ticket here* below it (the default): leave it there to create the ticket on that level, or pick a ticket to open the next level under it. E.g. `2579_OE_AI_IP` → *Common Development* → *Brian as an MCP* → *mcp integration* → *New ticket here* creates the ticket under *mcp integration*.
- The Notion ticket gets: your name as Task Name, you as Assignee, the priority, the project and parent ticket, the due date (today by default, can be changed or cleared), status **Not started** (so its Clockify task gets created), and an empty *Description* section.
- The board ticket goes to **Backlog** and is linked to its Clockify task once that appears (within a minute; otherwise at the next sync).
- The projects and their trees are loaded with the ticket list (daily, on start and by ⟳ Notion); a ticket you create is in the tree right away.
- **Skip Notion (personal)** adds a [personal ticket](#the-board) with the name and priority instead.

### Moving tickets

#### Tickets you are assigned to

| You move it to | Notion |
|---|---|
| In progress (drag, sheet or ▶ timer) | set to **In progress** if it was Backlog, Not started or Blocked. weekly tickets are not changed |
| Done | you are asked; the suggestion is **In review** when the ticket has a reviewer, **Done** when it hasn't, **Leave as is** for weekly tickets |
| Backlog | not changed |

#### Tickets you review

| You move it to | Notion |
|---|---|
| In progress | not changed (stays In review while you review it) |
| Backlog | set to **In progress** if it was In review — you ask for changes: the assignee works on it again, the ticket waits in your Backlog, and when the assignee sets it In review again you review it from there |
| Done | you are asked; the suggestion is **Done** |

**The Done question:** a dialog shows *Done*, *In review* and *Leave as is* with the suggestion highlighted; tap any of them to choose. Tapping outside the dialog leaves Notion as it is. Moving a ticket out of Done again does not change Notion.

Each change in Notion is confirmed with a short message at the bottom; if Notion can't be reached, the message tells you so and the board move is kept, but Notion is not retried: move the ticket again later or change the status in Notion.

### Filter

**All / Assigned / Review** in the header (on the Tasks page):

- **All** — every ticket (default).
- **Assigned** — tickets you are assigned to, and personal tickets (they are your own work).
- **Review** — tickets you review.

The column counts and times follow the filter. The choice is remembered in that browser. A ticket you linked to a Clockify task by hand counts as Assigned until the next sync (or until you move it), when the board learns whether you review it.
