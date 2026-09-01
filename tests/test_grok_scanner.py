"""Grok session adapter: discover, parse, idempotent rescan."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from token_dashboard.db import connect, expensive_prompts, init_db, overview_totals
from token_dashboard.grok_scanner import (
    _tokens_from_usage,
    parse_updates,
    scan_grok_dir,
)
from token_dashboard.scanner import scan_all

FIXTURE = Path(__file__).parent / "fixtures" / "grok_session"


def _write_usage_session(root: Path) -> Path:
    """Minimal updates.jsonl with turn_completed.usage (cache + IO)."""
    sess = root / "proj" / "sess-usage-001"
    sess.mkdir(parents=True)
    pid = "prompt-usage-1"
    lines = [
        {
            "timestamp": 1700000000,
            "method": "session/update",
            "params": {
                "sessionId": "sess-usage-001",
                "update": {
                    "sessionUpdate": "user_message_chunk",
                    "content": {"type": "text", "text": "hello"},
                },
                "_meta": {
                    "promptId": pid,
                    "totalTokens": 100,
                    "eventId": "e1",
                    "agentTimestampMs": 1700000000000,
                },
            },
        },
        {
            "timestamp": 1700000001,
            "method": "session/update",
            "params": {
                "sessionId": "sess-usage-001",
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "world reply here"},
                },
                "_meta": {
                    "promptId": pid,
                    "totalTokens": 120,
                    "modelId": "grok-4.5",
                    "eventId": "e2",
                    "agentTimestampMs": 1700000001000,
                },
            },
        },
        {
            "timestamp": 1700000002,
            "method": "_x.ai/session/update",
            "params": {
                "sessionId": "sess-usage-001",
                "update": {
                    "sessionUpdate": "turn_completed",
                    "prompt_id": pid,
                    "stop_reason": "end_turn",
                    "usage": {
                        "inputTokens": 65191,
                        "outputTokens": 1250,
                        "totalTokens": 66441,
                        "cachedReadTokens": 40704,
                        "reasoningTokens": 356,
                        "modelCalls": 3,
                        "modelUsage": {
                            "grok-4.5": {
                                "inputTokens": 65191,
                                "outputTokens": 1250,
                                "cachedReadTokens": 40704,
                            }
                        },
                    },
                },
                "_meta": {"eventId": "e3", "agentTimestampMs": 1700000002000},
            },
        },
    ]
    updates = sess / "updates.jsonl"
    with updates.open("w", encoding="utf-8") as f:
        for row in lines:
            f.write(json.dumps(row) + "\n")
    (sess / "summary.json").write_text(
        json.dumps({
            "info": {"id": "sess-usage-001", "cwd": "/tmp/proj"},
            "current_model_id": "grok-4.5",
        }),
        encoding="utf-8",
    )
    return updates


class TestGrokScanner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        self.root = os.path.join(self.tmp, "sessions")
        shutil.copytree(FIXTURE, self.root)
        init_db(self.db)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_scan_inserts_grok_source(self):
        n = scan_grok_dir(self.root, self.db)
        self.assertGreater(n["messages"], 0)
        self.assertGreater(n["tools"], 0)
        with connect(self.db) as c:
            srcs = {r[0] for r in c.execute("SELECT DISTINCT source FROM messages")}
            self.assertEqual(srcs, {"grok"})
            tools = {r[0] for r in c.execute(
                "SELECT DISTINCT tool_name FROM tool_calls WHERE tool_name != '_tool_result'"
            )}
            # fixture from a real session — at least one of these
            self.assertTrue(tools & {"read_file", "list_dir", "grep", "run_terminal_command", "todo_write", "search_replace"})

    def test_idempotent_rescan(self):
        n1 = scan_grok_dir(self.root, self.db)
        n2 = scan_grok_dir(self.root, self.db)
        self.assertEqual(n2["messages"], 0)
        self.assertEqual(n2["files"], 0)
        with connect(self.db) as c:
            m1 = c.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        # force reparse by bumping mtime via rewrite
        for p in Path(self.root).rglob("updates.jsonl"):
            data = p.read_bytes()
            p.write_bytes(data)
            os.utime(p, None)
        n3 = scan_grok_dir(self.root, self.db)
        self.assertGreater(n3["messages"], 0)
        with connect(self.db) as c:
            m2 = c.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        self.assertEqual(m1, m2)

    def test_parse_updates_user_and_tools(self):
        updates = next(Path(self.root).rglob("updates.jsonl"))
        msgs, tools = parse_updates(updates, "sess-fixture-001", "tmp-test-project", "/tmp/test-project", "grok-4.5")
        users = [m for m in msgs if m["type"] == "user"]
        assts = [m for m in msgs if m["type"] == "assistant"]
        self.assertGreaterEqual(len(users), 1)
        self.assertGreaterEqual(len(assts), 1)
        self.assertTrue(all(m["source"] == "grok" for m in msgs))
        self.assertTrue(all(t["source"] == "grok" for t in tools))
        self.assertTrue(any(m["uuid"].startswith("grok:") for m in msgs))

    def test_scan_all_combines(self):
        # empty claude dir + grok fixture
        claude = os.path.join(self.tmp, "claude_projects")
        os.makedirs(claude)
        n = scan_all(self.db, projects_dir=claude, grok_sessions_dir=self.root)
        self.assertGreater(n["messages"], 0)
        self.assertIn("grok", n["by_source"])
        t = overview_totals(self.db)
        self.assertGreater(t["sessions"], 0)
        self.assertTrue(any(s["source"] == "grok" for s in t["by_source"]))

    def test_tokens_from_usage_splits_cache(self):
        inp, out, cache = _tokens_from_usage({
            "inputTokens": 65191,
            "outputTokens": 1250,
            "cachedReadTokens": 40704,
        })
        self.assertEqual(cache, 40704)
        self.assertEqual(out, 1250)
        self.assertEqual(inp, 65191 - 40704)

    def test_user_chunk_without_prompt_id_inherits_next_turn_usage(self):
        """Grok user_message_chunk omits promptId; usage lives on the next pid."""
        sess = Path(self.tmp) / "nopid" / "s1"
        sess.mkdir(parents=True)
        pid = "real-prompt-id"
        lines = [
            {
                "timestamp": 1700001000,
                "params": {
                    "update": {
                        "sessionUpdate": "user_message_chunk",
                        "content": {"type": "text", "text": "how much cache?"},
                    },
                    "_meta": {"eventId": "e1"},
                },
            },
            {
                "timestamp": 1700001001,
                "params": {
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "here"},
                    },
                    "_meta": {"promptId": pid, "modelId": "grok-4.6"},
                },
            },
            {
                "timestamp": 1700001002,
                "params": {
                    "update": {
                        "sessionUpdate": "turn_completed",
                        "prompt_id": pid,
                        "usage": {
                            "inputTokens": 8000,
                            "outputTokens": 40,
                            "cachedReadTokens": 5000,
                        },
                    },
                    "_meta": {"promptId": pid},
                },
            },
        ]
        updates = sess / "updates.jsonl"
        with updates.open("w", encoding="utf-8") as f:
            for row in lines:
                f.write(json.dumps(row) + "\n")
        (sess / "summary.json").write_text(
            json.dumps({"info": {"id": "s1", "cwd": "/tmp/nopid"}}), encoding="utf-8"
        )
        msgs, _ = parse_updates(updates, "s1", "tmp-nopid", "/tmp/nopid", "grok-4.6")
        users = [m for m in msgs if m["type"] == "user" and m.get("prompt_text")]
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]["prompt_text"], "how much cache?")
        assts = [m for m in msgs if m["type"] == "assistant" and m["parent_uuid"] == users[0]["uuid"]]
        self.assertEqual(len(assts), 1)
        self.assertEqual(assts[0]["cache_read_tokens"], 5000)
        self.assertEqual(assts[0]["input_tokens"], 3000)
        self.assertEqual(assts[0]["output_tokens"], 40)
        self.assertFalse(any("_nopid_" in (m.get("uuid") or "") for m in msgs))

        n = scan_grok_dir(sess.parent, self.db)
        self.assertGreater(n["messages"], 0)
        rows = expensive_prompts(self.db, limit=10)
        grok = [r for r in rows if r["source"] == "grok"]
        self.assertEqual(len(grok), 1)
        self.assertEqual(grok[0]["cache_read_tokens"], 5000)
        self.assertEqual(grok[0]["billable_tokens"], 3040)

    def test_parse_turn_completed_usage(self):
        root = Path(self.tmp) / "usage_sessions"
        updates = _write_usage_session(root)
        msgs, _tools = parse_updates(
            updates, "sess-usage-001", "tmp-proj", "/tmp/proj", "grok-4.5"
        )
        assts = [m for m in msgs if m["type"] == "assistant"]
        self.assertEqual(len(assts), 1)
        a = assts[0]
        self.assertEqual(a["cache_read_tokens"], 40704)
        self.assertEqual(a["output_tokens"], 1250)
        self.assertEqual(a["input_tokens"], 65191 - 40704)
        self.assertEqual(a["cache_create_5m_tokens"], 0)
        self.assertEqual(a["cache_create_1h_tokens"], 0)
        self.assertEqual(a["stop_reason"], "end_turn")

    def test_tool_name_keeps_snake_case_not_human_title(self):
        sess = Path(self.tmp) / "toolsess" / "s1"
        sess.mkdir(parents=True)
        pid = "p1"
        lines = [
            {
                "timestamp": 1700000100,
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "user_message_chunk",
                        "content": {"type": "text", "text": "hi"},
                    },
                    "_meta": {"promptId": pid, "totalTokens": 10},
                },
            },
            {
                "timestamp": 1700000101,
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call",
                        "toolCallId": "t1",
                        "title": "read_file",
                        "rawInput": {"target_file": "/tmp/x.md"},
                    },
                    "_meta": {"promptId": pid},
                },
            },
            {
                "timestamp": 1700000102,
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "t1",
                        "kind": "read",
                        "title": "Read `/tmp/x.md`",
                        "rawInput": {"target_file": "/tmp/x.md"},
                    },
                    "_meta": {"promptId": pid},
                },
            },
            {
                "timestamp": 1700000103,
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "t2",
                        "kind": "execute",
                        "title": "Execute `ls -la`",
                        "rawInput": {"command": "ls -la"},
                        "status": "completed",
                        "content": {"type": "text", "text": "ok"},
                    },
                    "_meta": {"promptId": pid},
                },
            },
        ]
        updates = sess / "updates.jsonl"
        with updates.open("w", encoding="utf-8") as f:
            for row in lines:
                f.write(json.dumps(row) + "\n")
        msgs, tools = parse_updates(updates, "s1", "tmp", "/tmp", "grok-4.6")
        names = {t["tool_name"] for t in tools if t["tool_name"] != "_tool_result"}
        self.assertIn("read_file", names)
        self.assertNotIn("Read `/tmp/x.md`", names)
        self.assertIn("run_terminal_command", names)
        self.assertTrue(all(len(n) < 80 and " " not in n for n in names))

    def test_scan_persists_cache_read(self):
        root = Path(self.tmp) / "usage_sessions"
        _write_usage_session(root)
        n = scan_grok_dir(root, self.db)
        self.assertGreater(n["messages"], 0)
        with connect(self.db) as c:
            row = c.execute(
                "SELECT SUM(cache_read_tokens), SUM(input_tokens), SUM(output_tokens) "
                "FROM messages WHERE source='grok' AND type='assistant'"
            ).fetchone()
        self.assertEqual(row[0], 40704)
        self.assertEqual(row[1], 65191 - 40704)
        self.assertEqual(row[2], 1250)


if __name__ == "__main__":
    unittest.main()
