import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from token_dashboard.db import init_db, connect
from token_dashboard.memory import (
    _parse_frontmatter, _soft_links, _learning_timeline, memory_roi,
    quarantine_memory, promote_memory, get_brain, EXTRACTION_SENTINEL,
)
from token_dashboard.pricing import load_pricing

PRICING = load_pricing(Path(__file__).resolve().parent.parent / "pricing.json")


def _mem(meta_extra="", body="a fact"):
    return f"""---
name: sample
description: a sample memory
metadata:
  type: project
{meta_extra}---

{body}
"""


class FrontmatterTests(unittest.TestCase):
    def test_parses_source_and_origin(self):
        meta, body = _parse_frontmatter(_mem("  source: auto\n  origin_session: sess-1\n"))
        self.assertEqual(meta["type"], "project")
        self.assertEqual(meta["source"], "auto")
        self.assertEqual(meta["origin_session"], "sess-1")
        self.assertEqual(body, "a fact")

    def test_source_absent_is_not_set(self):
        meta, _ = _parse_frontmatter(_mem())
        self.assertNotIn("source", meta)


class SoftLinkTests(unittest.TestCase):
    def test_shared_vocabulary_links_unlinked_pair(self):
        entries = [
            {"id": "p::a", "name": "verification-engine", "description": "fuzzy devolutiva engine",
             "body": "the verification engine computes devolutiva fuzzy matching scores"},
            {"id": "p::b", "name": "verification-ui", "description": "verification container",
             "body": "the verification engine devolutiva fuzzy contract drives the ui container"},
            {"id": "p::c", "name": "unrelated", "description": "theme tokens",
             "body": "light dark color palette swatches"},
        ]
        links = _soft_links(entries, set())
        pairs = {tuple(sorted((l["source"], l["target"]))) for l in links}
        self.assertIn(("p::a", "p::b"), pairs)
        self.assertTrue(all(l["kind"] == "soft" for l in links))
        # the unrelated node should not link to the verification pair
        self.assertNotIn(("p::a", "p::c"), pairs)

    def test_explicit_pairs_are_skipped(self):
        entries = [
            {"id": "p::a", "name": "x", "description": "shared shared shared terms", "body": "shared terms here"},
            {"id": "p::b", "name": "y", "description": "shared shared shared terms", "body": "shared terms here"},
        ]
        links = _soft_links(entries, {("p::a", "p::b")})
        self.assertEqual(links, [])


class TimelineTests(unittest.TestCase):
    def test_counts_auto_user_and_learnings_per_day(self):
        projects = [{
            "entries": [
                {"mtime": "2026-06-15T10:00:00+00:00", "source": "auto"},
                {"mtime": "2026-06-15T11:00:00+00:00", "source": "user"},
                {"mtime": "2026-06-14T09:00:00+00:00", "source": "auto"},
            ],
            "learnings": [{"date": "2026-06-15"}, {"date": "2026-06-15"}],
        }]
        tl = _learning_timeline(projects)
        by_day = {d["day"]: d for d in tl}
        self.assertEqual(by_day["2026-06-15"]["auto"], 1)
        self.assertEqual(by_day["2026-06-15"]["user"], 1)
        self.assertEqual(by_day["2026-06-15"]["learnings"], 2)
        self.assertEqual(by_day["2026-06-14"]["auto"], 1)


class RoiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)
        self.recent = (datetime.utcnow() - timedelta(days=1)).isoformat()

    def _read(self, target, use_id, result_tokens):
        with connect(self.db) as c:
            c.execute("""INSERT INTO tool_calls (message_uuid, session_id, project_slug,
                tool_name, target, tool_use_id, timestamp) VALUES
                ('m','s','slug','Read',?,?,?)""", (target, use_id, self.recent))
            c.execute("""INSERT INTO tool_calls (message_uuid, session_id, project_slug,
                tool_name, target, tool_use_id, result_tokens, timestamp) VALUES
                ('m','s','slug','_tool_result',?,?,?,?)""",
                (use_id, use_id, result_tokens, self.recent))
            c.commit()

    def test_saved_counts_only_memorized_targets(self):
        self._read("/proj/engine.py", "u1", 5000)     # memorized
        self._read("/proj/random.py", "u2", 9000)      # not memorized
        covered = {"slug": "engine.py contract notes"}  # only engine.py is covered
        roi = memory_roi(self.db, covered, PRICING)
        self.assertEqual(roi["memorized_targets"], 1)
        self.assertEqual(roi["reread_count"], 1)
        self.assertEqual(roi["saved_tokens_est"], 5000)
        self.assertIsNotNone(roi["saved_usd_est"])

    def test_extraction_sessions_counted_as_cost(self):
        with connect(self.db) as c:
            c.execute("""INSERT INTO messages (uuid, session_id, project_slug, type,
                timestamp, prompt_text) VALUES
                ('u','ext','slug','user',?,?)""",
                (self.recent, f"... {EXTRACTION_SENTINEL} ... extract memories"))
            c.execute("""INSERT INTO messages (uuid, session_id, project_slug, type,
                timestamp, model, input_tokens, output_tokens) VALUES
                ('a','ext','slug','assistant',?,'claude-haiku-4-5',1000,200)""",
                (self.recent,))
            c.commit()
        roi = memory_roi(self.db, {}, PRICING)
        self.assertEqual(roi["extraction_sessions"], 1)
        self.assertEqual(roi["extraction_tokens"], 1200)
        self.assertGreater(roi["extraction_usd"], 0)


class MutationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.memdir = Path(self.tmp) / "slug" / "memory"
        self.memdir.mkdir(parents=True)
        (self.memdir / "auto_fact.md").write_text(
            _mem("  source: auto\n  origin_session: s1\n"), encoding="utf-8")
        (self.memdir / "MEMORY.md").write_text(
            "# Index\n- [Sample](auto_fact.md) — a hook\n- [Other](other.md) — keep me\n",
            encoding="utf-8")

    def test_quarantine_moves_file_and_strips_pointer(self):
        res = quarantine_memory(self.tmp, "slug", "auto_fact.md")
        self.assertTrue(res["ok"])
        self.assertFalse((self.memdir / "auto_fact.md").exists())
        self.assertTrue(any((self.memdir / ".trash").iterdir()))
        index = (self.memdir / "MEMORY.md").read_text()
        self.assertNotIn("auto_fact.md", index)
        self.assertIn("other.md", index)  # untouched

    def test_promote_sets_source_user(self):
        res = promote_memory(self.tmp, "slug", "auto_fact.md")
        self.assertTrue(res["ok"])
        text = (self.memdir / "auto_fact.md").read_text()
        self.assertIn("source: user", text)
        self.assertNotIn("source: auto", text)

    def test_path_traversal_is_rejected(self):
        self.assertFalse(quarantine_memory(self.tmp, "slug", "../../etc/passwd")["ok"])
        self.assertFalse(quarantine_memory(self.tmp, "../slug", "auto_fact.md")["ok"])
        self.assertFalse(quarantine_memory(self.tmp, "slug", "MEMORY.md")["ok"])
        self.assertFalse(quarantine_memory(self.tmp, "slug", "nonexistent.md")["ok"])


class GetBrainShapeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)
        memdir = Path(self.tmp) / "projects" / "myproj" / "memory"
        memdir.mkdir(parents=True)
        (memdir / "a.md").write_text(_mem("  source: auto\n"), encoding="utf-8")

    def test_payload_has_roi_timeline_and_link_kinds(self):
        brain = get_brain(str(Path(self.tmp) / "projects"), self.db, PRICING)
        self.assertIn("roi", brain)
        self.assertIn("timeline", brain)
        self.assertIn("suggestions", brain)
        entry = brain["projects"][0]["entries"][0]
        self.assertEqual(entry["source"], "auto")


if __name__ == "__main__":
    unittest.main()
