"""Minimal Notion REST client for the Tasks database: my tickets for the board, one ticket, setting a ticket's status.
Reads NOTION_API_KEY and NOTION_USER_ID from the environment when called."""
import json
import os
import urllib.error
import urllib.request

BASE = "https://api.notion.com/v1"
TASKS = "7c3bce6e-e4e2-46aa-92f7-c0ca33139f7e"  # data source of the General-Task database


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


def _query(filter):
    out, cursor = [], None
    while True:
        body = {"filter": filter, "page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = _call(f"/data_sources/{TASKS}/query", "POST", body)
        out += r["results"]
        if not r.get("has_more"):
            return out
        cursor = r["next_cursor"]


def _task(page):
    """{id, gen: "GEN-<n>", title, status, prio, role: "assignee" | "reviewer" | "" (my role on it),
    reviewed: it has a reviewer, weekly: is_weekly is ticked}"""
    p = page["properties"]
    uid = p["Task ID"]["unique_id"]
    me = os.environ["NOTION_USER_ID"]
    reviewers = [u["id"] for u in p["Reviewer"]["people"]]
    return {"id": page["id"], "gen": "%s-%s" % (uid["prefix"], uid["number"]),
            "title": "".join(x["plain_text"] for x in p["Task Name"]["title"]).strip(),
            "status": (p["Status"]["status"] or {}).get("name", ""),
            "prio": (p["Priority"]["select"] or {}).get("name", ""),
            "role": "assignee" if any(u["id"] == me for u in p["Assignee"]["people"]) else "reviewer" if me in reviewers else "",
            "reviewed": bool(reviewers), "weekly": bool(p.get("is_weekly", {}).get("checkbox"))}


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
