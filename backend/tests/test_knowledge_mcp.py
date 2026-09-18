"""The knowledge MCP server: the grant, the five tools, and the typed errors.

This is the one place in factor-ia that speaks a protocol nobody else here
speaks, and the one place an agent's argument reaches a permanent store. So
these cover two things above all:

- **the grant is the boundary** — a base outside it is not addressable, whatever
  the argument says;
- **every refusal carries its code**, because the agent's next move differs for
  each, and a tool that said "something went wrong" would leave it guessing
  between "stop naming this base" and "get it again and retry".
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from factoria.knowledge import mcp, store as knowledge

MANIFEST = """---
id: {id}
title: {title}
scope: {scope}
format: markdown
---

What this base is for.
"""


def make_base(workspace: Path, base_id: str, scope: str) -> Path:
    root = workspace / "_knowledge" / base_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.md").write_text(
        MANIFEST.format(id=base_id, title=base_id, scope=scope), encoding="utf-8"
    )
    (root / "notes.md").write_text("# Notes\n\nThe client moved to v3.\n", encoding="utf-8")
    return root


def make_server(
    workspace: Path, *bases: tuple[str, str, bool], journal: Path | None = None
) -> mcp.Server:
    """A server over `(id, scope, writable)` triples, with the bases on disk."""
    grants = {}
    for base_id, scope, writable in bases:
        root = make_base(workspace, base_id, scope)
        grants[base_id] = mcp.BaseGrant(
            id=base_id,
            root=root,
            scope=scope,
            title=base_id,
            writable=writable and knowledge.is_writable(scope),
        )
    return mcp.Server(
        mcp.Grant(run_id="r1", node_id="n1", journal=journal, bases=grants)
    )


def refusal(server: mcp.Server, tool: str, **arguments) -> mcp.Refusal:
    with pytest.raises(mcp.Refusal) as raised:
        server.call(tool, arguments)
    return raised.value


# ---- the grant is the boundary ---------------------------------------------


def test_a_base_that_does_not_exist_is_unknown(workspace: Path) -> None:
    server = make_server(workspace, ("acme", "codebase", True))
    error = refusal(server, "knowledge_list", base="nope")
    assert error.code == "unknown_base"
    # And it says what the machine *does* have, so the next move is not a guess.
    assert "acme" in error.message


def test_a_real_base_this_node_was_not_wired_to_is_not_addressable(workspace: Path) -> None:
    """The distinction the whole grant exists to draw: `not_wired` is a real
    base this machine was not given, and the move is to carry on and say so."""
    make_base(workspace, "secrets", "general")
    server = make_server(workspace, ("acme", "codebase", True))
    error = refusal(server, "knowledge_list", base="secrets")
    assert error.code == "not_wired"
    assert "carry on without it" in error.message.lower() or "Carry on" in error.message


def test_every_tool_needs_a_base(workspace: Path) -> None:
    server = make_server(workspace, ("acme", "codebase", True))
    assert refusal(server, "knowledge_list").code == "bad_request"


def test_an_unknown_tool_is_refused_by_name(workspace: Path) -> None:
    server = make_server(workspace, ("acme", "codebase", True))
    assert refusal(server, "knowledge_delete", base="acme").code == "bad_request"


# ---- scope and handles -----------------------------------------------------


def test_a_principles_base_refuses_a_write_even_when_the_grant_says_otherwise(
    workspace: Path,
) -> None:
    """Belt and braces: the grant is written by this server's own parent, and a
    permission that is only ever *copied* is a permission one bug can widen."""
    server = make_server(workspace, ("principles", "principles", True))
    error = refusal(server, "knowledge_append", base="principles", path="x.md", content="y")
    assert error.code == "read_only_scope"
    assert "measured against" in error.message


def test_a_read_only_wire_refuses_a_write_with_a_different_sentence(workspace: Path) -> None:
    """Same code, different advice: this one is a canvas fact the operator can
    change, and the message says so rather than talking about principles."""
    server = make_server(workspace, ("acme", "codebase", False))
    error = refusal(server, "knowledge_append", base="acme", path="x.md", content="y")
    assert error.code == "read_only_scope"
    assert "no write wire" in error.message


def test_reading_is_allowed_on_a_base_wired_read_only(workspace: Path) -> None:
    server = make_server(workspace, ("acme", "codebase", False))
    body = server.call("knowledge_list", {"base": "acme"})
    assert body["writable"] is False
    assert [entry["path"] for entry in body["files"]] == ["notes.md"]


# ---- the envelope ----------------------------------------------------------


def test_every_response_carries_the_same_envelope(workspace: Path) -> None:
    server = make_server(workspace, ("acme", "codebase", True))
    for tool, arguments in (
        ("knowledge_list", {}),
        ("knowledge_search", {"query": "v3"}),
        ("knowledge_get", {"path": "notes.md"}),
    ):
        body = server.call(tool, {"base": "acme", **arguments})
        assert set(body) >= {"base", "scope", "writable", "path", "bytes", "truncated", "next"}
        assert body["base"] == "acme" and body["scope"] == "codebase"


def test_get_says_it_was_cut_and_where_to_continue(workspace: Path) -> None:
    server = make_server(workspace, ("acme", "codebase", True))
    body = server.call("knowledge_get", {"base": "acme", "path": "notes.md", "limit": 5})
    assert body["truncated"] is True and body["next"] == 5
    rest = server.call(
        "knowledge_get", {"base": "acme", "path": "notes.md", "offset": body["next"]}
    )
    assert rest["truncated"] is False and rest["next"] is None


def test_search_returns_pointers(workspace: Path) -> None:
    server = make_server(workspace, ("acme", "codebase", True))
    body = server.call("knowledge_search", {"base": "acme", "query": "v3"})
    assert body["matches"][0]["path"] == "notes.md"
    assert body["matches"][0]["line"] == 3


def test_a_missing_path_is_not_found_and_says_to_list(workspace: Path) -> None:
    server = make_server(workspace, ("acme", "codebase", True))
    error = refusal(server, "knowledge_get", base="acme", path="nothing.md")
    assert error.code == "not_found" and "knowledge_list" in error.message


def test_a_traversing_path_does_not_echo_itself_back(workspace: Path) -> None:
    """Reflecting a rejected path is how a probe maps the box — `api._translate`'s
    rule, carried to the one surface an agent can spell paths at."""
    server = make_server(workspace, ("acme", "codebase", True))
    error = refusal(server, "knowledge_get", base="acme", path="../../etc/passwd")
    assert error.code == "not_found"
    assert "passwd" not in error.message


def test_an_oversized_write_is_too_large(workspace: Path) -> None:
    server = make_server(workspace, ("acme", "codebase", True))
    error = refusal(
        server,
        "knowledge_append",
        base="acme",
        path="big.md",
        content="x" * (knowledge.FILE_MAX_BYTES + 1),
    )
    assert error.code == "too_large"


def test_a_moved_anchor_is_stale(workspace: Path) -> None:
    server = make_server(workspace, ("acme", "codebase", True))
    error = refusal(
        server, "knowledge_update", base="acme", path="notes.md", old="v9", new="v10"
    )
    assert error.code == "stale"


def test_a_held_base_is_locked_rather_than_queued(workspace: Path) -> None:
    """A run that blocks on a shelf is a run whose watchdog fires for a reason
    nobody can see, so contention is reported and the agent retries once."""
    server = make_server(workspace, ("acme", "codebase", True))
    knowledge.base_lock("acme").acquire()
    try:
        error = refusal(
            server, "knowledge_append", base="acme", path="notes.md", content="x"
        )
        assert error.code == "locked" and "Retry once" in error.message
    finally:
        knowledge.base_lock("acme").release()


# ---- writing, and the journal ----------------------------------------------


def test_a_write_lands_and_is_visible_to_the_next_read(workspace: Path) -> None:
    server = make_server(workspace, ("acme", "codebase", True))
    body = server.call(
        "knowledge_append",
        {"base": "acme", "path": "notes.md", "content": "Then to v4 in September."},
    )
    assert body["written"] is True and body["path"] == "notes.md"
    read = server.call("knowledge_get", {"base": "acme", "path": "notes.md"})
    assert "v4 in September" in read["content"]


def test_the_journal_records_a_successful_write(workspace: Path, tmp_path: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    server = make_server(workspace, ("acme", "codebase", True), journal=journal)
    server.call("knowledge_append", {"base": "acme", "path": "notes.md", "content": "x"})
    entry = json.loads(journal.read_text(encoding="utf-8").splitlines()[0])
    assert entry["ok"] is True and entry["tool"] == "knowledge_append"
    assert entry["base"] == "acme" and entry["node_id"] == "n1"


def test_the_journal_records_a_refused_write(workspace: Path, tmp_path: Path) -> None:
    """This is the line that fails the node. Without it a machine told to record
    a decision could report success while recording nothing."""
    journal = tmp_path / "journal.jsonl"
    server = make_server(workspace, ("acme", "codebase", True), journal=journal)
    refusal(server, "knowledge_update", base="acme", path="notes.md", old="gone", new="x")
    entry = json.loads(journal.read_text(encoding="utf-8").splitlines()[0])
    assert entry["ok"] is False and entry["code"] == "stale"


def test_a_read_is_journaled_as_a_read(workspace: Path, tmp_path: Path) -> None:
    """**This used to assert the opposite**, and the reversal is the point.

    A read took no lock and left no line, so a base could not say who had
    consulted it — the one half of its story that lived nowhere but the agent's
    own transcript. It is journaled now, marked `kind: "read"` so the two
    readers of this file can never disagree about what it was: the storage log
    turns it into a row on the base, and `_knowledge_failures` skips it, because
    a refused read must never fail a node the way a refused write does.
    """
    journal = tmp_path / "journal.jsonl"
    server = make_server(workspace, ("acme", "codebase", True), journal=journal)
    server.call("knowledge_list", {"base": "acme"})

    entry = json.loads(journal.read_text(encoding="utf-8").splitlines()[0])
    assert entry["kind"] == "read"
    assert entry["tool"] == "knowledge_list"
    assert entry["ok"] is True and entry["code"] is None


def test_a_write_is_journaled_as_a_write(workspace: Path, tmp_path: Path) -> None:
    """The flag both readers key off, written down rather than re-derived from
    the tool name in two places — one of which fails a node over the answer."""
    journal = tmp_path / "journal.jsonl"
    server = make_server(workspace, ("acme", "codebase", True), journal=journal)
    server.call(
        "knowledge_append", {"base": "acme", "path": "notes.md", "content": "hi"}
    )

    entry = json.loads(journal.read_text(encoding="utf-8").splitlines()[0])
    assert entry["kind"] == "write" and entry["ok"] is True


# ---- the tool names the spawn has to agree with ----------------------------


def test_tool_names_are_spelled_once(workspace: Path) -> None:
    """The deny list and the server must not drift over a string, so both are
    built from `qualified`."""
    assert mcp.qualified("knowledge_get") == "mcp__knowledge__knowledge_get"
    read_only = mcp.allowed_tool_names(writable=False)
    assert "mcp__knowledge__knowledge_search" in read_only
    assert "mcp__knowledge__knowledge_append" not in read_only
    assert set(mcp.allowed_tool_names(writable=True)) - set(read_only) == set(
        mcp.denied_tool_names()
    )


def test_every_schema_is_a_tool_the_server_implements() -> None:
    named = {schema["name"] for schema in mcp.TOOL_SCHEMAS}
    assert named == set(mcp.ALL_TOOLS)


# ---- the transport ---------------------------------------------------------


def rpc(stdio: mcp.Stdio, *messages: dict) -> list[dict]:
    source = io.StringIO("".join(json.dumps(message) + "\n" for message in messages))
    sink = io.StringIO()
    stdio.serve(source, sink)
    return [json.loads(line) for line in sink.getvalue().splitlines()]


def test_the_handshake_echoes_the_clients_protocol_version(workspace: Path) -> None:
    stdio = mcp.Stdio(make_server(workspace, ("acme", "codebase", True)))
    [reply] = rpc(
        stdio,
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2099-01-01"}},
    )
    assert reply["result"]["protocolVersion"] == "2099-01-01"
    assert reply["result"]["serverInfo"]["name"] == "knowledge"


def test_a_notification_is_answered_with_nothing(workspace: Path) -> None:
    stdio = mcp.Stdio(make_server(workspace, ("acme", "codebase", True)))
    assert rpc(stdio, {"jsonrpc": "2.0", "method": "notifications/initialized"}) == []


def test_tools_list_carries_every_schema(workspace: Path) -> None:
    stdio = mcp.Stdio(make_server(workspace, ("acme", "codebase", True)))
    [reply] = rpc(stdio, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert len(reply["result"]["tools"]) == len(mcp.ALL_TOOLS)


def test_a_refused_call_is_a_result_with_is_error_not_a_protocol_error(
    workspace: Path,
) -> None:
    """The distinction that makes "retry once" mean anything: a refused write is
    a tool result the agent can act on, not the tool being broken."""
    stdio = mcp.Stdio(make_server(workspace, ("acme", "codebase", False)))
    [reply] = rpc(
        stdio,
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "knowledge_append",
                    "arguments": {"base": "acme", "path": "x.md", "content": "y"}}},
    )
    assert "error" not in reply
    assert reply["result"]["isError"] is True
    body = json.loads(reply["result"]["content"][0]["text"])
    assert body["error"]["code"] == "read_only_scope"


def test_an_unknown_method_is_a_protocol_error(workspace: Path) -> None:
    stdio = mcp.Stdio(make_server(workspace, ("acme", "codebase", True)))
    [reply] = rpc(stdio, {"jsonrpc": "2.0", "id": 4, "method": "resources/list"})
    assert reply["error"]["code"] == -32601


def test_unparseable_input_does_not_end_the_server(workspace: Path) -> None:
    stdio = mcp.Stdio(make_server(workspace, ("acme", "codebase", True)))
    replies = rpc(stdio, {"jsonrpc": "2.0", "id": 5, "method": "ping"})
    assert replies[0]["result"] == {}


# ---- the grant document ----------------------------------------------------


def test_a_grant_is_loaded_from_disk_and_re_derives_writability(
    workspace: Path, tmp_path: Path, monkeypatch
) -> None:
    make_base(workspace, "principles", "principles")
    path = tmp_path / "grant.json"
    path.write_text(
        json.dumps(
            {
                "run_id": "r1",
                "node_id": "n1",
                "journal": str(tmp_path / "j.jsonl"),
                "bases": [
                    {
                        "id": "principles",
                        "root": str(workspace / "_knowledge" / "principles"),
                        "scope": "principles",
                        "writable": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(mcp.GRANT_ENV, str(path))
    grant = mcp.Grant.from_env()
    assert grant.bases["principles"].writable is False


def test_no_grant_is_an_empty_one_rather_than_a_crash(monkeypatch) -> None:
    """A server spawned with nothing wired answers every call with `not_wired`,
    which is legible in a transcript. A process that exited would surface as
    "the MCP server failed to start", which says nothing about wiring."""
    monkeypatch.delenv(mcp.GRANT_ENV, raising=False)
    assert mcp.Grant.from_env().bases == {}
