import httpx
import pytest
import respx

from weki.sync.changes import (
    Change,
    changes_from_log,
    changes_from_page,
    collapse,
    list_changes,
)
from weki.sync.client import WikiClient

API = "https://simple.wikipedia.org/w/api.php"
T1, T2, T3 = "2026-11-01T00:00:00Z", "2026-11-02T00:00:00Z", "2026-11-03T00:00:00Z"


def log(type_, action, title="Ulm", ns=0, ts=T1, **params):
    return {
        "type": type_,
        "action": action,
        "title": title,
        "ns": ns,
        "timestamp": ts,
        "params": params,
    }


def kinds(changes):
    return [(c.path, c.kind) for c in changes]


def test_revisions_detect_new_pages_by_parent_zero():
    page = {
        "title": "New Town",
        "revisions": [
            {"revid": 1, "parentid": 0, "timestamp": T1},
            {"revid": 2, "parentid": 1, "timestamp": T2},
        ],
    }
    assert kinds(changes_from_page(page)) == [("New_Town", "new"), ("New_Town", "edit")]


@pytest.mark.parametrize("action", ["delete", "delete_redir"])
def test_delete_log(action):
    assert kinds(changes_from_log(log("delete", action))) == [("Ulm", "delete")]


def test_restore_log():
    assert kinds(changes_from_log(log("delete", "restore"))) == [("Ulm", "restore")]


@pytest.mark.parametrize("action", ["revision", "event"])
def test_revision_visibility_logs_are_ignored(action):
    assert changes_from_log(log("delete", action)) == []


def test_delete_outside_main_namespace_is_ignored():
    assert changes_from_log(log("delete", "delete", "Talk:Ulm", ns=1)) == []


@pytest.mark.parametrize("action", ["move", "move_redir"])
def test_move_refetches_both_ends(action):
    event = log("move", action, "Old Name", target_ns=0, target_title="New Name")
    assert kinds(changes_from_log(event)) == [("Old_Name", "edit"), ("New_Name", "move")]


def test_move_from_draft_only_fetches_target():
    event = log("move", "move", "Draft:Thing", ns=118, target_ns=0, target_title="Thing")
    assert kinds(changes_from_log(event)) == [("Thing", "move")]


def test_move_out_of_main_only_refetches_source():
    event = log("move", "move", "Thing", target_ns=2, target_title="User:X/Thing")
    assert kinds(changes_from_log(event)) == [("Thing", "edit")]


def test_merge_refetches_source_and_destination():
    event = log("merge", "merge", "Old", dest_ns=0, dest_title="Dest")
    assert kinds(changes_from_log(event)) == [("Old", "edit"), ("Dest", "merge")]


def test_suppressed_log_without_title_is_skipped():
    assert changes_from_log({"type": "delete", "action": "delete", "timestamp": T1}) == []


def c(kind, ts, rev=None, path="Ulm"):
    return Change(path, path, kind, ts, rev)


def test_collapse_last_event_wins_regardless_of_input_order():
    latest = collapse([c("delete", T3), c("edit", T1, 1), c("edit", T2, 2)])
    assert latest["Ulm"].kind == "delete"


def test_collapse_delete_then_restore_refetches():
    assert collapse([c("delete", T1), c("restore", T2)])["Ulm"].kind == "restore"


def test_collapse_new_stays_new_through_edits_with_latest_rev():
    latest = collapse([c("new", T1, 1), c("edit", T2, 2)])["Ulm"]
    assert (latest.kind, latest.rev_id) == ("new", 2)


def test_collapse_keeps_paths_apart():
    assert len(collapse([c("edit", T1, 1, "A"), c("edit", T1, 2, "B")])) == 2


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@respx.mock
async def test_list_changes_merges_sources_and_reads_moves_across_namespaces():
    def respond(request):
        params = request.url.params
        if params["list"] == "allrevisions":
            entries = [
                {"title": "Ulm", "revisions": [{"revid": 5, "parentid": 4, "timestamp": T1}]}
            ]
        elif params["letype"] == "delete":
            assert params["lenamespace"] == "0"
            entries = [log("delete", "delete", "Gone", ts=T2)]
        elif params["letype"] == "move":
            assert "lenamespace" not in params
            event = log("move", "move", "Draft:Fresh", ns=118, ts=T2)
            event["params"] = {"target_ns": 0, "target_title": "Fresh"}
            entries = [event]
        else:
            entries = []
        return httpx.Response(200, json={"query": {params["list"]: entries}})

    respx.get(API).mock(side_effect=respond)
    wiki = WikiClient("simple.wikipedia.org", "ua", delay=0)
    changes = await list_changes(wiki, T1, T3)
    await wiki.aclose()
    assert {p: x.kind for p, x in changes.items()} == {
        "Ulm": "edit",
        "Gone": "delete",
        "Fresh": "move",
    }
