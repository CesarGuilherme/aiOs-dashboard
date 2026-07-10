"""Grok session adapter: discover, parse, idempotent rescan."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from token_dashboard.db import connect, init_db, overview_totals
from token_dashboard.grok_scanner import parse_updates, scan_grok_dir
from token_dashboard.scanner import scan_all

FIXTURE = Path(__file__).parent / "fixtures" / "grok_session"


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


if __name__ == "__main__":
    unittest.main()
