import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from token_dashboard.db import init_db, connect
from token_dashboard.memory import (
    _coverage_slug,
    _target_vanished,
    _parse_frontmatter, _soft_links, _learning_timeline, memory_roi,
    quarantine_memory, promote_memory, get_brain, EXTRACTION_SENTINEL,
)
from token_dashboard.pricing import load_pricing

from tests.brain_fixture import make_brain

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
        self.nodes = make_brain(self.tmp, [
            {"id": "d", "kind": "domain"},
            {"id": "d/p", "kind": "project"},
            {"id": "d/p/auto_fact", "kind": "memory", "source": "auto", "body": "a fact"},
        ])
        self.note = Path(self.nodes) / "d/p/auto_fact.md"

    def test_quarantine_moves_file_to_trash(self):
        res = quarantine_memory(self.nodes, "d/p", "d/p/auto_fact.md")
        self.assertTrue(res["ok"])
        self.assertFalse(self.note.exists())
        self.assertTrue(any((Path(self.nodes) / ".trash").iterdir()))

    def test_promote_sets_source_user(self):
        res = promote_memory(self.nodes, "d/p", "d/p/auto_fact.md")
        self.assertTrue(res["ok"])
        text = self.note.read_text()
        self.assertIn("source: user", text)
        self.assertNotIn("source: auto", text)

    def test_path_traversal_is_rejected(self):
        self.assertFalse(quarantine_memory(self.nodes, "d/p", "../../etc/passwd")["ok"])
        self.assertFalse(quarantine_memory(self.nodes, "d/p", "d/p/../../../x.md")["ok"])
        self.assertFalse(quarantine_memory(self.nodes, "other", "d/p/auto_fact.md")["ok"])
        self.assertFalse(quarantine_memory(self.nodes, "d/p", "d/p/_project.md")["ok"])
        self.assertFalse(quarantine_memory(self.nodes, "d/p", "d/p/nonexistent.md")["ok"])


class GetBrainShapeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)
        self.nodes = make_brain(self.tmp, [
            {"id": "global", "kind": "domain"},
            {"id": "global/rule", "kind": "memory", "type": "feedback", "body": "always BRL"},
            {"id": "secom", "kind": "domain"},
            {"id": "secom/app", "kind": "project", "workspaces": ["/w/app"], "size": 3},
            {"id": "secom/app/topic", "kind": "topic"},
            {"id": "secom/app/topic/a", "kind": "memory", "type": "gotcha", "source": "auto"},
            {"id": "secom/app/LEARNINGS#2026-09-01-x", "kind": "memory", "type": "learning",
             "name": "x", "date": "2026-09-01"},
            {"id": "secom/db", "kind": "project", "workspaces": ["/w/db"]},
        ], edges=[("secom/app", "secom/db", "depends-on", "reads the warehouse")])

    def test_payload_groups_by_hub_and_keeps_learnings(self):
        brain = get_brain(self.nodes, self.db, PRICING)
        self.assertIn("roi", brain)
        self.assertIn("timeline", brain)
        self.assertIn("suggestions", brain)
        by_slug = {p["slug"]: p for p in brain["projects"]}
        self.assertEqual(set(by_slug), {"global", "secom/app", "secom/db"})
        app = by_slug["secom/app"]
        self.assertEqual([e["name"] for e in app["entries"]], ["a"])  # topic note rolls up to hub
        self.assertEqual(app["entries"][0]["source"], "auto")
        self.assertEqual(app["entries"][0]["file"], "secom/app/topic/a.md")
        self.assertEqual(app["learnings"][0]["date"], "2026-09-01")

    def test_graph_has_every_node_parent_edges_and_typed_links(self):
        brain = get_brain(self.nodes, self.db, PRICING)
        g = brain["graph"]
        self.assertEqual(len(g["nodes"]), 8)
        kinds = {n["id"]: n["kind"] for n in g["nodes"]}
        self.assertEqual(kinds["secom/app"], "project")
        parent = {(l["source"], l["target"]) for l in g["links"] if l["kind"] == "parent"}
        self.assertIn(("secom/app/topic", "secom/app/topic/a"), parent)
        typed = [l for l in brain["links"] if l["kind"] == "depends-on"]
        self.assertEqual(typed[0]["why"], "reads the warehouse")

    def test_stale_tree_is_detected_after_hand_edit(self):
        from token_dashboard.memory_parsing import _stale
        db, nodes = Path(self.tmp) / "brain.db", Path(self.nodes)
        past = db.stat().st_mtime - 60
        for f in [nodes, *nodes.rglob("*")]:
            os.utime(f, (past - 60, past - 60))
        os.utime(db, (past, past))
        self.assertFalse(_stale(nodes, db))
        (nodes / "secom/app/topic/a.md").write_text("edited by hand")
        self.assertTrue(_stale(nodes, db))

    def test_missing_brain_db_is_empty_not_an_error(self):
        brain = get_brain(os.path.join(self.tmp, "nowhere", "nodes"), self.db, PRICING)
        self.assertEqual(brain["projects"], [])
        self.assertEqual(brain["graph"]["nodes"], [])


class CoverageSlugTest(unittest.TestCase):
    """Raw tool_calls.project_slug does not address the Brain dir that holds
    the memories — Grok stores `SSD_CESAR`, worktrees get their own slug."""

    def test_grok_underscore_slug_maps_to_canonical_brain_dir(self):
        self.assertEqual(
            _coverage_slug("-Volumes-SSD_CESAR-Developer-Secom-oracle-mysql",
                           "/Volumes/SSD_CESAR/Developer/Secom/oracle/mysql/gold.sql"),
            "-Volumes-SSD-CESAR-Developer-Secom-oracle-mysql")

    def test_worktree_slug_falls_back_to_parent_repo(self):
        self.assertEqual(
            _coverage_slug("-Volumes-SSD_CESAR-Developer-vision-.worktrees-stage-1",
                           "/Volumes/SSD_CESAR/Developer/vision/.worktrees/stage-1/lib/a.ts"),
            "-Volumes-SSD-CESAR-Developer-vision")

    def test_canonical_slug_is_unchanged(self):
        slug = "-Volumes-SSD-CESAR-Developer-Secom-oracle-mysql"
        self.assertEqual(_coverage_slug(slug, "/x/y.sql"), slug)

    def test_vanished_only_when_volume_is_mounted(self):
        self.assertFalse(_target_vanished("/Volumes/NOT-MOUNTED-XYZ/a/b.ts"))
        self.assertTrue(_target_vanished(str(Path(__file__).parent / "no-such-file.ts")))
        self.assertFalse(_target_vanished(__file__))


if __name__ == "__main__":
    unittest.main()
