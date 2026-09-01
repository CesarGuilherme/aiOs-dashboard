import os
import tempfile
import unittest
from pathlib import Path

from token_dashboard.db import init_db, connect
from token_dashboard.tips import (
    cache_discipline_tips, repeat_file_tips, ignored_memory_tips,
    right_size_tips, outlier_tips, all_tips, dismiss_tip,
    failing_command_tips, poll_wait_tips, skill_reread_tips,
)

TODAY = "2026-04-19T00:00:00"
TS = "2026-04-15T00:00:00Z"


def _msg(c, uuid="m1", session="s1", project="p", ts=TS, **kw):
    cols = dict(model="claude-opus-4-7", type="assistant",
                input_tokens=0, output_tokens=0, cache_read_tokens=0,
                cache_create_5m_tokens=0, cache_create_1h_tokens=0, is_sidechain=0)
    cols.update(kw)
    c.execute(
        """INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, model,
            input_tokens, output_tokens, cache_read_tokens, cache_create_5m_tokens,
            cache_create_1h_tokens, is_sidechain)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (uuid, session, project, cols["type"], ts, cols["model"],
         cols["input_tokens"], cols["output_tokens"], cols["cache_read_tokens"],
         cols["cache_create_5m_tokens"], cols["cache_create_1h_tokens"], cols["is_sidechain"]),
    )


def _tool(c, *, uuid="m1", session="s1", project="p", tool="Read", target="src/Root.tsx",
          ts=TS, err=0, use_id=None, result_tokens=None):
    c.execute(
        """INSERT INTO tool_calls (message_uuid, session_id, project_slug, tool_name, target,
            timestamp, is_error, tool_use_id, result_tokens)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (uuid, session, project, tool, target, ts, err, use_id, result_tokens),
    )


class CacheTipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)

    def _ins(self, ts, project, cache_read, cache_create):
        with connect(self.db) as c:
            _msg(c, uuid=f"uuid-{ts}", project=project, ts=ts,
                 cache_read_tokens=cache_read, cache_create_5m_tokens=cache_create,
                 input_tokens=100, output_tokens=100)
            c.commit()

    def test_low_cache_hit_emits_tip(self):
        self._ins("2026-04-15T00:00:00Z", "projX", 10, 1_000_000)
        tips = cache_discipline_tips(self.db, today_iso=TODAY)
        self.assertTrue(any(t["category"] == "cache" for t in tips))

    def test_healthy_cache_no_tip(self):
        for i in range(10):
            self._ins(f"2026-04-15T00:00:0{i}Z", "projY", 1_000_000, 50)
        tips = cache_discipline_tips(self.db, today_iso=TODAY)
        self.assertFalse(any(t["category"] == "cache" for t in tips))


class RepeatFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)
        self.mem = os.path.join(self.tmp, "projects")

    def _reads(self, target, sessions, n_each=4, tool="Read"):
        with connect(self.db) as c:
            k = 0
            for s in sessions:
                _msg(c, uuid=f"m-{s}", session=s)
                for _ in range(n_each):
                    _tool(c, uuid=f"m-{s}", session=s, target=target, tool=tool)
                    k += 1
            c.commit()

    def test_one_session_is_work_not_a_tip(self):
        self._reads("src/Root.tsx", ["s1"], n_each=15)
        tips = repeat_file_tips(self.db, self.mem, today_iso=TODAY)
        self.assertFalse(any(t["category"] == "repeat-file" for t in tips))

    def test_index_is_noise(self):
        self._reads("/Users/x/.brain/INDEX.md", ["s1", "s2", "s3"], n_each=5)
        self.assertEqual(repeat_file_tips(self.db, self.mem, today_iso=TODAY), [])
        self.assertEqual(ignored_memory_tips(self.db, self.mem, today_iso=TODAY), [])

    def test_uncovered_multi_session_emits_repeat_file(self):
        self._reads("src/Root.tsx", ["s1", "s2", "s3"], n_each=4)
        tips = repeat_file_tips(self.db, self.mem, today_iso=TODAY)
        cats = [t["category"] for t in tips]
        self.assertIn("repeat-file", cats)
        self.assertTrue(tips[0].get("prompt"))

    def test_covered_file_is_ignored_memory_not_repeat(self):
        mem = Path(self.mem) / "p" / "memory"
        mem.mkdir(parents=True)
        (mem / "root_contract.md").write_text("contract for Root.tsx layout", encoding="utf-8")
        self._reads("src/Root.tsx", ["s1", "s2", "s3"], n_each=4)
        self.assertEqual(repeat_file_tips(self.db, self.mem, today_iso=TODAY), [])
        ign = ignored_memory_tips(self.db, self.mem, today_iso=TODAY)
        self.assertTrue(any(t["category"] == "ignored-memory" for t in ign))


class FailTipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)
        self.brain = Path(self.tmp) / "projects"
        self.brain.mkdir()

    def _fails(self, tool, target, sessions, n_each=3):
        with connect(self.db) as c:
            for s in sessions:
                _msg(c, uuid=f"m-{s}", session=s, project="p")
                for i in range(n_each):
                    uid = f"{s}-{i}"
                    _tool(c, uuid=f"m-{s}", session=s, project="p", tool=tool,
                          target=target, use_id=uid)
                    _tool(c, uuid=f"m-{s}", session=s, project="p", tool="_tool_result",
                          target=target, use_id=uid, err=1)
            c.commit()

    def test_protocol_tool_is_noise(self):
        self._fails("exit_plan_mode", "ExitPlanMode", ["s1", "s2"], n_each=3)
        self.assertEqual(failing_command_tips(self.db, str(self.brain), today_iso=TODAY), [])

    def test_documented_fail_is_ignored_fail(self):
        g = Path(self.tmp) / "global"
        g.mkdir()
        (g / "xcode.md").write_text(
            "do not call xcode-tools__XcodeRefreshCodeIssuesInFile", encoding="utf-8")
        self._fails("use_tool", "xcode-tools__XcodeRefreshCodeIssuesInFile",
                    ["s1", "s2"], n_each=3)
        tips = failing_command_tips(self.db, str(self.brain), today_iso=TODAY)
        self.assertTrue(any(t["category"] == "ignored-fail" for t in tips))
        self.assertFalse(any(t["category"] == "repeat-fail" for t in tips))

    def test_undocumented_fail_is_repeat_fail(self):
        self._fails("Bash", "npm test", ["s1", "s2"], n_each=3)
        tips = failing_command_tips(self.db, str(self.brain), today_iso=TODAY)
        self.assertTrue(any(t["category"] == "repeat-fail" for t in tips))


class PollAndSkillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)

    def test_sleep_loop_emits_poll_wait(self):
        with connect(self.db) as c:
            _msg(c)
            for i in range(5):
                _tool(c, tool="Bash", target="for i in $(seq 1 4); do sleep 29; done")
            c.commit()
        tips = poll_wait_tips(self.db, today_iso=TODAY)
        self.assertTrue(any(t["category"] == "poll-wait" for t in tips))

    def test_skill_md_reread(self):
        path = "/Users/x/.grok/skills/learn/SKILL.md"
        with connect(self.db) as c:
            for s in ("s1", "s2", "s3"):
                _msg(c, uuid=s, session=s)
                for _ in range(3):
                    _tool(c, uuid=s, session=s, tool="read_file", target=path)
            c.commit()
        tips = skill_reread_tips(self.db, today_iso=TODAY)
        self.assertTrue(any(t["category"] == "skill-reread" and "learn" in t["title"] for t in tips))


class RightSizeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)
        os.environ["USD_BRL"] = "5.4"

    def test_short_opus_turns_flagged_in_brl(self):
        with connect(self.db) as c:
            for i in range(10):
                _msg(c, uuid=f"a{i}", input_tokens=1_000_000, output_tokens=200,
                     ts="2026-04-18T00:00:00Z")
            c.commit()
        tips = right_size_tips(self.db, today_iso=TODAY)
        self.assertTrue(any(t["category"] == "right-size" for t in tips))
        self.assertTrue(any("R$" in t["body"] for t in tips))

    def test_short_grok_turns_flagged(self):
        with connect(self.db) as c:
            for i in range(10):
                _msg(c, uuid=f"g{i}", model="grok-4.6",
                     input_tokens=1_000_000, output_tokens=200,
                     ts="2026-04-18T00:00:00Z")
            c.commit()
        tips = right_size_tips(self.db, today_iso=TODAY)
        self.assertTrue(any(t["scope"] == "grok-short-turns-7d" for t in tips))


class OutlierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)

    def test_giant_tool_result_flagged(self):
        with connect(self.db) as c:
            for i in range(20):
                _msg(c, uuid=f"u{i}", type="user", ts="2026-04-18T00:00:00Z")
                _tool(c, uuid=f"u{i}", tool="_tool_result", target="tu",
                      result_tokens=100000, ts="2026-04-18T00:00:00Z")
            c.commit()
        tips = outlier_tips(self.db, today_iso=TODAY)
        self.assertTrue(any(t["category"] == "tool-bloat" for t in tips))

    def test_outlier_tips_detects_large_subagent(self):
        with connect(self.db) as c:
            for i in range(12):
                _msg(c, uuid=f"msg{i}", ts="2026-04-18T00:00:00Z",
                     is_sidechain=1, input_tokens=100,
                     output_tokens=100000 if i == 5 else 1000)
                c.execute("UPDATE messages SET agent_id='agent-big' WHERE uuid=?", (f"msg{i}",))
            c.commit()
        tips = outlier_tips(self.db, today_iso=TODAY)
        self.assertTrue(any(t["category"] == "subagent-outlier" for t in tips))


class DismissAndAllTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)
        self.mem = os.path.join(self.tmp, "projects")
        os.makedirs(self.mem)

    def test_dismissed_tip_doesnt_reappear(self):
        with connect(self.db) as c:
            _msg(c, uuid="m", project="projZ", input_tokens=100, output_tokens=100,
                 cache_read_tokens=10, cache_create_5m_tokens=1_000_000)
            c.commit()
        tips_before = cache_discipline_tips(self.db, today_iso=TODAY)
        self.assertTrue(tips_before)
        dismiss_tip(self.db, tips_before[0]["key"])
        self.assertFalse(cache_discipline_tips(self.db, today_iso=TODAY))

    def test_all_tips_does_not_copy_brain_knowledge(self):
        """8 reads / 3 sessions used to be a Brain knowledge tip. Tips must not clone it."""
        with connect(self.db) as c:
            for s in ("s1", "s2", "s3"):
                _msg(c, uuid=s, session=s)
                for _ in range(3):  # 9 total, under the >10 hot-read bar
                    _tool(c, uuid=s, session=s, target="lib/agent.ts")
            c.commit()
        tips = all_tips(self.db, self.mem, today_iso=TODAY)
        self.assertFalse(any(t["category"] in ("memory", "repeat-file", "ignored-memory") for t in tips))


if __name__ == "__main__":
    unittest.main()
