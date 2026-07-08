import json
import tempfile
import unittest
from pathlib import Path

from token_dashboard.workspace import scan_workspace


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


if __name__ == "__main__":
    unittest.main()
