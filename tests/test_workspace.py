import json
import os
import tempfile
import unittest
from pathlib import Path

from token_dashboard.workspace import scan_workspace, allowed_open_path


def _build_fake_claude(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    (root / ".claude.json").write_text(json.dumps({
        "mcpServers": {"svelte": {}, "n8n": {}},
        "projects": {
            "/Users/x/dev/proj-a": {"mcpServers": {"svelte": {}, "playwright": {}}},
            "/Users/x/dev/proj-b": {},
        },
    }))
    claude = root / ".claude"
    (claude / "scheduled-tasks" / "daily-log").mkdir(parents=True)
    (claude / "scheduled-tasks" / "weekly-report.md").write_text("x")
    (claude / "skills" / "watch").mkdir(parents=True)
    (claude / "skills" / "learn").mkdir()
    plug = claude / "plugins" / "cache" / "mkt" / "superpowers" / "6.0.3" / "skills"
    (plug / "brainstorming").mkdir(parents=True)
    (plug / "watch").mkdir()   # duplicate of user skill — user wins
    return claude


class WorkspaceScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.claude = _build_fake_claude(self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def test_applications_dedup_global_wins(self):
        apps = scan_workspace(self.claude)["applications"]
        by_name = {a["name"]: a for a in apps}
        self.assertEqual(by_name["svelte"]["scope"], "global")
        self.assertEqual(by_name["n8n"]["scope"], "global")
        self.assertEqual(by_name["playwright"]["scope"], "proj-a")
        self.assertEqual(len(apps), 3)

    def test_routines_dirs_and_md_files(self):
        names = {r["name"] for r in scan_workspace(self.claude)["routines"]}
        self.assertEqual(names, {"daily-log", "weekly-report"})

    def test_skills_dedup_user_wins(self):
        skills = scan_workspace(self.claude)["skills"]
        by_name = {s["name"]: s for s in skills}
        self.assertEqual(by_name["watch"]["source"], "user")
        self.assertEqual(by_name["learn"]["source"], "user")
        self.assertEqual(by_name["brainstorming"]["source"], "plugin")
        self.assertEqual(len(skills), 3)

    def test_missing_everything_yields_empty_lists(self):
        empty = Path(self.tmp.name) / "nope" / ".claude"
        self.assertEqual(scan_workspace(empty),
                         {"applications": [], "routines": [], "skills": []})

    def test_grok_mcp_workflows_and_skills_merge(self):
        grok = Path(self.tmp.name) / ".grok"
        grok.mkdir()
        (grok / "config.toml").write_text(
            '[mcp_servers.svelte]\ncommand = "npx"\n\n'
            '[mcp_servers.tasks]\nurl = "http://127.0.0.1"\n\n'
            '[mcp_servers.tasks.env]\nTOKEN = "x"\n',
            encoding="utf-8",
        )
        (grok / "workflows").mkdir()
        (grok / "workflows" / "deep-research.rhai").write_text("let meta = #{};\n")
        (grok / "skills" / "last30days").mkdir(parents=True)
        (grok / "skills" / "last30days" / "SKILL.md").write_text("# x")
        ws = scan_workspace(self.claude, grok)
        apps = {a["name"]: a for a in ws["applications"]}
        self.assertEqual(apps["svelte"]["scope"], "global")  # Claude global wins
        self.assertEqual(apps["tasks"]["scope"], "grok")
        self.assertIn("deep-research", {r["name"] for r in ws["routines"]})
        self.assertIn("last30days", {s["name"] for s in ws["skills"]})


class OpenPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.claude = Path(self.tmp.name) / ".claude"
        self.dev = Path(self.tmp.name) / "Developer"
        (self.dev / "proj-a").mkdir(parents=True)
        (self.dev / "proj-a" / "small.md").write_text("y")

    def tearDown(self):
        self.tmp.cleanup()

    def test_allowed_open_path(self):
        inside = self.dev / "proj-a" / "small.md"
        self.assertTrue(allowed_open_path(str(inside), [self.dev], self.claude))
        self.assertFalse(allowed_open_path("/etc/passwd", [self.dev], self.claude))
        sneaky = str(self.dev / ".." / "outside.txt")
        self.assertFalse(allowed_open_path(sneaky, [self.dev], self.claude))

    def test_allowed_open_grok_and_brain_homes(self):
        grok = Path(self.tmp.name) / ".grok"
        brain = Path(self.tmp.name) / ".brain"
        (grok / "docs").mkdir(parents=True)
        (brain / "global").mkdir(parents=True)
        (grok / "docs" / "a.md").write_text("g")
        (brain / "global" / "b.md").write_text("b")
        self.assertTrue(allowed_open_path(str(grok / "docs" / "a.md"), [self.dev], self.claude, grok, brain))
        self.assertTrue(allowed_open_path(str(brain / "global" / "b.md"), [self.dev], self.claude, grok, brain))
        self.assertFalse(allowed_open_path("/etc/passwd", [self.dev], self.claude, grok, brain))


if __name__ == "__main__":
    unittest.main()
