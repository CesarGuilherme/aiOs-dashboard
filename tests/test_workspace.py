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
        self.assertEqual(scan_workspace(empty, roots=[]),
                         {"applications": [], "routines": [], "skills": [], "files": []})


class FileScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.claude = Path(self.tmp.name) / ".claude"   # empty — files only
        self.dev = Path(self.tmp.name) / "Developer"
        proj = self.dev / "proj-a"
        (proj / "src").mkdir(parents=True)
        (proj / "src" / "big.py").write_text("x" * 500)
        (proj / "small.md").write_text("y")
        (proj / ".hidden").write_text("z")
        (proj / "node_modules" / "dep").mkdir(parents=True)
        (proj / "node_modules" / "dep" / "index.js").write_text("no")
        (self.dev / "proj-b").mkdir()
        (self.dev / "proj-b" / "only.txt").write_text("t")
        (self.dev / ".DS_Store").write_text("")   # hidden root entry ignored

    def tearDown(self):
        self.tmp.cleanup()

    def test_files_scanned_with_fields_and_skips(self):
        files = scan_workspace(self.claude, roots=[self.dev])["files"]
        rels = {f["rel"] for f in files}
        self.assertEqual(rels, {"src/big.py", "small.md", "only.txt"})
        big = next(f for f in files if f["name"] == "big.py")
        self.assertEqual(big["dept"], "proj-a")
        self.assertEqual(big["ext"], "py")
        self.assertEqual(big["size"], 500)
        self.assertTrue(Path(big["path"]).is_absolute())
        self.assertIn("T", big["mtime"])   # ISO timestamp

    def test_cap_largest_first(self):
        proj = self.dev / "proj-c"
        proj.mkdir()
        for i in range(30):
            (proj / f"f{i:02}.txt").write_text("x" * (i + 1))
        from token_dashboard import workspace
        old = workspace.MAX_FILES_PER_DEPT
        workspace.MAX_FILES_PER_DEPT = 10
        try:
            files = [f for f in scan_workspace(self.claude, roots=[self.dev])["files"]
                     if f["dept"] == "proj-c"]
        finally:
            workspace.MAX_FILES_PER_DEPT = old
        self.assertEqual(len(files), 10)
        self.assertEqual(min(f["size"] for f in files), 21)   # kept the 10 largest

    def test_missing_root_yields_empty(self):
        files = scan_workspace(self.claude, roots=[Path(self.tmp.name) / "nope"])["files"]
        self.assertEqual(files, [])

    def test_allowed_open_path(self):
        inside = self.dev / "proj-a" / "small.md"
        self.assertTrue(allowed_open_path(str(inside), [self.dev], self.claude))
        self.assertFalse(allowed_open_path("/etc/passwd", [self.dev], self.claude))
        sneaky = str(self.dev / ".." / "outside.txt")
        self.assertFalse(allowed_open_path(sneaky, [self.dev], self.claude))


if __name__ == "__main__":
    unittest.main()
