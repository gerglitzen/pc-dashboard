"""Minimal Notion REST client for the Tasks database: my tickets for the board, one ticket, setting a ticket's status,
the projects I own with their ticket trees, creating a ticket.
Reads NOTION_API_KEY and NOTION_USER_ID from the environment when called."""
import json
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = "https://api.notion.com/v1"
TASKS = "7c3bce6e-e4e2-46aa-92f7-c0ca33139f7e"  # data source of the General-Task database
PROJECTS = "7910a986-ebef-4166-a36e-0d67f7fb31d3"  # data source of the General-Projects database
_titles = {}  # page id -> title of projects and milestones, reloaded by pickable()


class NotionError(Exception):
    pass


def enabled():
    return bool(os.environ.get("NOTION_API_KEY") and os.environ.get("NOTION_USER_ID"))


def _call(path, method="GET", body=None):
    req = urllib.request.Request(BASE + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + os.environ["NOTION_API_KEY"],
                                          "Notion-Version": "2025-09-03", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise NotionError(f"HTTP {e.code} {e.read()[:200].decode(errors='replace')}") from None
    except OSError as e:  # network error / timeout
        raise NotionError(str(e)) from None


def _query(filter, source=TASKS):
    out, cursor = [], None
    while True:
        body = {"filter": filter, "page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = _call(f"/data_sources/{source}/query", "POST", body)
        out += r["results"]
        if not r.get("has_more"):
            return out
        cursor = r["next_cursor"]


def _task(page):
    """{id, gen: "GEN-<n>", title, status, prio, role: "assignee" | "reviewer" | "" (my role on it),
    reviewed: it has a reviewer, weekly: is_weekly is ticked, project / parent: page id or "",
    assignees / reviewers: their names, prs: page ids of its GitHub Pull Requests}"""
    p = page["properties"]
    uid = p["Task ID"]["unique_id"]
    me = os.environ["NOTION_USER_ID"]
    reviewers = [u["id"] for u in p["Reviewer"]["people"]]
    return {"id": page["id"], "gen": "%s-%s" % (uid["prefix"], uid["number"]),
            "title": "".join(x["plain_text"] for x in p["Task Name"]["title"]).strip(),
            "status": (p["Status"]["status"] or {}).get("name", ""),
            "prio": (p["Priority"]["select"] or {}).get("name", ""),
            "role": "assignee" if any(u["id"] == me for u in p["Assignee"]["people"]) else "reviewer" if me in reviewers else "",
            "reviewed": bool(reviewers), "weekly": bool(p.get("is_weekly", {}).get("checkbox")),
            "project": next((r["id"] for r in p["Project"]["relation"]), ""),
            "parent": next((r["id"] for r in p["Parent-task"]["relation"]), ""),
            "assignees": [u.get("name", "") for u in p["Assignee"]["people"]],
            "reviewers": [u.get("name", "") for u in p["Reviewer"]["people"]],
            "prs": [r["id"] for r in p["GitHub Pull Requests"]["relation"]]}


def my_tasks(day):
    """Tickets for the board, due on or before day (YYYY-MM-DD): assigned to me and Backlog / Not started / In progress,
    or In review where I am the reviewer."""
    me = os.environ["NOTION_USER_ID"]
    due = {"property": "Due", "date": {"on_or_before": day}}
    return [_task(p) for p in _query({"or": [
        {"and": [{"property": "Assignee", "people": {"contains": me}}, due] +
                [{"property": "Status", "status": {"does_not_equal": s}} for s in ("Blocked", "In review", "Done", "Archived")]},
        {"and": [{"property": "Reviewer", "people": {"contains": me}}, due,
                 {"property": "Status", "status": {"equals": "In review"}}]}]})]


def get_task(gen):
    """The ticket with Task ID gen (e.g. "GEN-4544")."""
    pages = _query({"property": "Task ID", "unique_id": {"equals": int(gen.rpartition("-")[2])}})
    if not pages:
        raise NotionError(f"{gen} not found")
    return _task(pages[0])


def set_status(page_id, status):
    _call(f"/pages/{page_id}", "PATCH", {"properties": {"Status": {"status": {"name": status}}}})


def pr_urls(page_ids):
    """GitHub URLs of linked GitHub Pull Requests pages."""
    with ThreadPoolExecutor(4) as pool:
        pages = list(pool.map(lambda i: _call(f"/pages/{i}"), page_ids))
    return [u for pg in pages for u in [(pg["properties"].get("_external_object_url") or {}).get("url")] if u]


def _title(page_id):
    if page_id not in _titles:
        props = _call(f"/pages/{page_id}")["properties"].values()
        _titles[page_id] = "".join(x["plain_text"] for p in props if p["type"] == "title" for x in p["title"]).strip()
    return _titles[page_id]


def pickable():
    """Reloads the project and milestone names too. My tickets to add to the board by hand: assigned to me and not Blocked / Done / Archived, or In review where I am
    the reviewer. Each gets project: the project's name and label: "<milestone> - <task>" (only the task without one)."""
    me = os.environ["NOTION_USER_ID"]
    tasks = [_task(p) for p in _query({"or": [
        {"and": [{"property": "Assignee", "people": {"contains": me}}] +
                [{"property": "Status", "status": {"does_not_equal": s}} for s in ("Blocked", "Done", "Archived")]},
        {"and": [{"property": "Reviewer", "people": {"contains": me}},
                 {"property": "Status", "status": {"equals": "In review"}}]}]})]
    _titles.clear()
    with ThreadPoolExecutor(4) as pool:  # names of the projects and milestones
        list(pool.map(_title, {i for t in tasks for i in (t["project"], t["parent"]) if i}))
    for t in tasks:
        t["project"] = _title(t["project"]) if t["project"] else "(no project)"
        t["label"] = (_title(t["parent"]) + " - " if t["parent"] else "") + t["title"]
    return tasks


def _name(page):
    return "".join(x["plain_text"] for p in page["properties"].values() if p["type"] == "title" for x in p["title"]).strip()


def my_projects():
    """The In progress projects I own (only the owner creates tickets in them), each with its open tickets (not Done /
    Archived) as a flat tree: [{id, name, tickets: [{id, gen, title, parent: page id or "" (top level)}]}], by name."""
    me = os.environ["NOTION_USER_ID"]
    projects = [{"id": p["id"], "name": _name(p), "tickets": []} for p in _query({"and": [
        {"property": "Owner", "people": {"contains": me}},
        {"property": "Status", "status": {"equals": "In progress"}}]}, PROJECTS)]
    by_id = {p["id"]: p for p in projects}
    if projects:
        for page in _query({"and": [{"or": [{"property": "Project", "relation": {"contains": p["id"]}} for p in projects]},
                                    {"property": "Status", "status": {"does_not_equal": "Done"}},
                                    {"property": "Status", "status": {"does_not_equal": "Archived"}}]}):
            t = _task(page)
            if t["project"] in by_id:
                by_id[t["project"]]["tickets"].append({"id": t["id"], "gen": t["gen"], "title": t["title"], "parent": t["parent"]})
    return sorted(projects, key=lambda p: p["name"].lower())


def create_task(title, project_id, parent_id, prio, due):
    """Create a ticket assigned to me, Not started (so its Clockify task gets created), in the project, under the
    ticket parent_id ("" = top level), with an empty Description section. Returns it like get_task."""
    props = {"Task Name": {"title": [{"text": {"content": title}}]},
             "Status": {"status": {"name": "Not started"}},
             "Assignee": {"people": [{"id": os.environ["NOTION_USER_ID"]}]},
             "Priority": {"select": {"name": prio}},
             "Project": {"relation": [{"id": project_id}]}}
    if due:
        props["Due"] = {"date": {"start": due}}
    if parent_id:
        props["Parent-task"] = {"relation": [{"id": parent_id}]}
    return _task(_call("/pages", "POST", {
        "parent": {"type": "data_source_id", "data_source_id": TASKS}, "properties": props,
        "children": [{"object": "block", "type": "heading_2",
                      "heading_2": {"rich_text": [{"type": "text", "text": {"content": "Description"}}]}}]}))
