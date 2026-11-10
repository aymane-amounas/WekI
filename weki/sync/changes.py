"""List what changed on the wiki since a timestamp."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from weki.sync.client import WikiClient

LIMIT = 500
DELETE_ACTIONS = {"delete", "delete_redir", "delete_redir2"}


@dataclass(frozen=True)
class Change:
    path: str
    title: str
    kind: str
    ts: str
    rev_id: int | None = None

    @property
    def is_delete(self) -> bool:
        return self.kind == "delete"


def title_to_path(title: str) -> str:
    return title.replace(" ", "_")


def _change(title: str, kind: str, ts: str, rev_id: int | None = None) -> Change:
    return Change(title_to_path(title), title, kind, ts, rev_id)


def changes_from_page(page: dict) -> list[Change]:
    return [
        _change(
            page["title"],
            "new" if rev.get("parentid") == 0 else "edit",
            rev["timestamp"],
            rev["revid"],
        )
        for rev in page.get("revisions", [])
    ]


def changes_from_log(event: dict) -> list[Change]:
    title, ts = event.get("title"), event.get("timestamp")
    if not title or not ts:
        return []
    log_type, action = event.get("type"), event.get("action")
    params = event.get("params") or {}
    in_main = event.get("ns") == 0

    if log_type == "delete" and in_main:
        if action in DELETE_ACTIONS:
            return [_change(title, "delete", ts)]
        if action == "restore":
            return [_change(title, "restore", ts)]
    if log_type == "move":
        found = [_change(title, "edit", ts)] if in_main else []
        if params.get("target_ns") == 0 and params.get("target_title"):
            found.append(_change(params["target_title"], "move", ts))
        return found
    if log_type == "merge":
        found = [_change(title, "edit", ts)] if in_main else []
        if params.get("dest_ns") == 0 and params.get("dest_title"):
            found.append(_change(params["dest_title"], "merge", ts))
        return found
    return []


def collapse(changes: Iterable[Change]) -> dict[str, Change]:
    latest: dict[str, Change] = {}
    for change in sorted(changes, key=lambda c: c.ts):
        previous = latest.get(change.path)
        if previous and previous.kind == "new" and change.kind == "edit":
            change = Change(change.path, change.title, "new", change.ts, change.rev_id)
        latest[change.path] = change
    return latest


async def list_changes(client: WikiClient, since: str, until: str) -> dict[str, Change]:
    changes: list[Change] = []
    revisions = {
        "list": "allrevisions",
        "arvnamespace": 0,
        "arvlimit": LIMIT,
        "arvdir": "newer",
        "arvstart": since,
        "arvend": until,
        "arvprop": "ids|timestamp",
    }
    async for page in client.paged(revisions, "allrevisions"):
        changes.extend(changes_from_page(page))

    log = {
        "list": "logevents",
        "lelimit": LIMIT,
        "ledir": "newer",
        "lestart": since,
        "leend": until,
        "leprop": "title|type|timestamp|details",
    }
    for log_type, extra in (
        ("delete", {"lenamespace": 0}),
        ("merge", {"lenamespace": 0}),
        ("move", {}),
    ):
        async for event in client.paged({**log, **extra, "letype": log_type}, "logevents"):
            changes.extend(changes_from_log(event))
    return collapse(changes)
