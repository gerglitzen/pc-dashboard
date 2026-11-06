"""Minimal Clockify REST client: the project/task picker list and creating, editing, deleting time entries.
Reads CLOCKIFY_API_KEY and CLOCKIFY_USER_ID from the environment when called."""
import json
import os
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

BASE = "https://api.clockify.me/api/v1"
_lock = threading.Lock()
_ws = None
_catalog = None


class ClockifyError(Exception):
    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code


def enabled():
    return bool(os.environ.get("CLOCKIFY_API_KEY") and os.environ.get("CLOCKIFY_USER_ID"))


def _call(path, method="GET", body=None):
    req = urllib.request.Request(BASE + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Api-Key": os.environ["CLOCKIFY_API_KEY"],
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = r.read()
            return json.loads(data) if data else None
    except urllib.error.HTTPError as e:
        raise ClockifyError(f"HTTP {e.code} {e.read()[:200].decode(errors='replace')}", e.code) from None
    except OSError as e:  # network error / timeout
        raise ClockifyError(str(e)) from None


def _workspace():
    global _ws
    if not _ws:
        _ws = _call("/user")["activeWorkspace"]
    return _ws


def catalog(refresh=False):
    """Active projects with the open tasks this user can log time to: tasks assigned to them,
    to one of their user groups, or to anyone. Cached until refresh=True."""
    global _catalog
    with _lock:
        if _catalog is None or refresh:
            ws, me = _workspace(), os.environ["CLOCKIFY_USER_ID"]
            groups = {g["id"] for g in _call(f"/workspaces/{ws}/user-groups?page-size=5000")
                      if me in g.get("userIds", [])}
            projects = _call(f"/workspaces/{ws}/projects?archived=false&page-size=5000")
            with ThreadPoolExecutor(8) as ex:
                task_lists = list(ex.map(
                    lambda p: _call(f"/workspaces/{ws}/projects/{p['id']}/tasks?is-active=true&page-size=5000"),
                    projects))
            out = []
            for p, tasks in zip(projects, task_lists):
                mine = [{"id": t["id"], "name": t["name"], "billable": t.get("billable")} for t in tasks
                        if t["status"] == "ACTIVE" and (me in t["assigneeIds"] or groups & set(t["userGroupIds"])
                                                        or not (t["assigneeIds"] or t["userGroupIds"]))]
                if mine:
                    out.append({"id": p["id"], "name": p["name"], "client": p.get("clientName") or "",
                                "tasks": sorted(mine, key=lambda t: t["name"].lower())})
            _catalog = sorted(out, key=lambda p: (p["client"].lower(), p["name"].lower()))
        return _catalog


def _entry(project_id, task_id, description, start, end):
    def iso(dt):
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    body = {"start": iso(start), "end": iso(end), "projectId": project_id, "taskId": task_id,
            "description": description}
    billable = next((t["billable"] for p in catalog() if p["id"] == project_id
                     for t in p["tasks"] if t["id"] == task_id), None)
    if billable is not None:  # same default the Clockify UI applies when picking the task
        body["billable"] = billable
    return body


def add_entry(project_id, task_id, description, start, end):
    """Create a time entry for the API key's user; start/end are timezone-aware datetimes. Returns its id."""
    return _call(f"/workspaces/{_workspace()}/time-entries", "POST",
                 _entry(project_id, task_id, description, start, end))["id"]


def update_entry(entry_id, project_id, task_id, description, start, end):
    _call(f"/workspaces/{_workspace()}/time-entries/{entry_id}", "PUT",
          _entry(project_id, task_id, description, start, end))


def entry_day(entry_id):
    """Local date on which the entry ends."""
    e = _call(f"/workspaces/{_workspace()}/time-entries/{entry_id}")
    return datetime.fromisoformat(e["timeInterval"]["end"].replace("Z", "+00:00")).astimezone().date()


def delete_entry(entry_id):
    try:
        _call(f"/workspaces/{_workspace()}/time-entries/{entry_id}", "DELETE")
    except ClockifyError as e:
        if e.code != 404:  # already gone
            raise
