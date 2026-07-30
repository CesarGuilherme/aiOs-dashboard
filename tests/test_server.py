import http.server
import json
import os
import socket
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

from token_dashboard.db import init_db
from token_dashboard.server import build_handler


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO messages (uuid, parent_uuid, session_id, project_slug, type, timestamp, model, input_tokens, output_tokens, cache_read_tokens, cache_create_5m_tokens, cache_create_1h_tokens, prompt_text, prompt_chars) VALUES ('u',NULL,'s','p','user','2026-04-19T00:00:00Z',NULL,0,0,0,0,0,'hi',2)")
            c.execute("INSERT INTO messages (uuid, parent_uuid, session_id, project_slug, type, timestamp, model, input_tokens, output_tokens, cache_read_tokens, cache_create_5m_tokens, cache_create_1h_tokens) VALUES ('a','u','s','p','assistant','2026-04-19T00:00:01Z','claude-haiku-4-5',1,1,0,0,0)")
            c.commit()
        self.port = _free_port()
        H = build_handler(self.db, projects_dir="/nonexistent")
        self.httpd = http.server.HTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()

    def _get(self, path, headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers=headers or {})
        return urllib.request.urlopen(req).read()

    def test_root_serves_htmx_ui(self):
        body = self._get("/")
        self.assertIn(b"htmx.min.js", body)
        self.assertIn(b'id="app"', body)

    def test_spa_still_served(self):
        body = self._get("/spa")
        self.assertIn(b"AI-Dashboard", body)
        self.assertIn(b"/web/app.js", body)
        self.assertNotIn(b"htmx.min.js", body)
        self.assertEqual(body, self._get("/index.html"))

    def test_overview_json(self):
        body = json.loads(self._get("/api/overview"))
        self.assertIn("sessions", body)
        self.assertEqual(body["sessions"], 1)

    def test_prompts_json(self):
        body = json.loads(self._get("/api/prompts?limit=10"))
        self.assertIsInstance(body, list)

    def test_projects_json(self):
        body = json.loads(self._get("/api/projects"))
        self.assertIsInstance(body, list)
        self.assertEqual(body[0]["project_slug"], "p")

    def test_plan_json(self):
        body = json.loads(self._get("/api/plan"))
        self.assertIn("plan", body)
        self.assertIn("pricing", body)

    def test_head_returns_200_not_501(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/", method="HEAD")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.read(), b"")

    def test_head_api_endpoint(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/overview", method="HEAD")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.read(), b"")


    # --- /hx (htmx) frontend -------------------------------------------------

    def test_full_page(self):
        body = self._get("/hx")
        self.assertIn(b"<!doctype html>", body)
        self.assertIn(b"htmx.min.js", body)
        self.assertIn(b'class="hud-theme"', body)

    def test_fragment_has_no_shell(self):
        body = self._get("/hx/projects", headers={"HX-Request": "true"})
        self.assertNotIn(b"<!doctype", body)
        self.assertIn(b"<table", body)
        self.assertIn(b"<td", body)

    def test_all_tabs_200(self):
        from token_dashboard.hx_views import TABS
        for tab in TABS:
            with self.subTest(tab=tab):
                self.assertTrue(self._get(f"/hx/{tab}", headers={"HX-Request": "true"}))

    def test_active_tab_marked(self):
        self.assertIn(b'hx-push-url="true" class="active">projects', self._get("/hx/projects"))

    def test_unknown_tab_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/hx/nope")
        self.assertEqual(cm.exception.code, 404)

    def test_escapes_user_data(self):
        with sqlite3.connect(self.db) as c:
            c.execute(
                "INSERT INTO messages (uuid, parent_uuid, session_id, project_slug, type,"
                " timestamp, model, input_tokens, output_tokens, cache_read_tokens,"
                " cache_create_5m_tokens, cache_create_1h_tokens, prompt_text, prompt_chars)"
                " VALUES ('x',NULL,'s2',?,'user','2026-04-19T00:00:02Z',NULL,0,0,0,0,0,'hi',2)",
                ("<img src=x onerror=alert(1)>",),
            )
            c.commit()
        body = self._get("/hx/projects", headers={"HX-Request": "true"})
        self.assertIn(b"&lt;img src=x", body)
        self.assertNotIn(b"<img src=x", body)

    def test_escapes_prompt_text(self):
        with sqlite3.connect(self.db) as c:
            c.execute(
                "INSERT INTO messages (uuid, parent_uuid, session_id, project_slug, type,"
                " timestamp, model, input_tokens, output_tokens, cache_read_tokens,"
                " cache_create_5m_tokens, cache_create_1h_tokens, prompt_text, prompt_chars)"
                " VALUES ('p1',NULL,'s','p','user','2026-04-19T00:00:03Z',NULL,0,0,0,0,0,?,30)",
                ("<script>alert(1)</script>",),
            )
            c.execute(
                "INSERT INTO messages (uuid, parent_uuid, session_id, project_slug, type,"
                " timestamp, model, input_tokens, output_tokens, cache_read_tokens,"
                " cache_create_5m_tokens, cache_create_1h_tokens)"
                " VALUES ('p2','p1','s','p','assistant','2026-04-19T00:00:04Z',"
                "'claude-haiku-4-5',1,1,500,0,0)"
            )
            c.commit()
        body = self._get("/hx/prompts", headers={"HX-Request": "true"})
        self.assertIn(b"&lt;script&gt;", body)
        self.assertNotIn(b"<script>alert(1)</script>", body)

    def _post_form(self, path, fields):
        data = urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read()

    def test_post_dismiss_is_form_encoded(self):
        """Guards the ordering gotcha: /hx/* must claim the body before json.loads."""
        status, body = self._post_form("/hx/tips/dismiss", {"key": "some-tip"})
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")

    def test_post_plan_swaps_pill_oob(self):
        status, body = self._post_form("/hx/plan", {"plan": "max20"})
        self.assertEqual(status, 200)
        self.assertIn(b'id="plan-pill"', body)
        self.assertIn(b'hx-swap-oob="true"', body)
        self.assertIn(b"max20", body)
        self.assertIn(b"max20", self._get("/hx/settings"))

    def test_brain_ships_rings_data(self):
        body = self._get("/hx/brain", headers={"HX-Request": "true"})
        self.assertIn(b'id="rings-data"', body)
        self.assertIn(b'id="rings-canvas"', body)
        # The JSON island must never contain a raw closing tag.
        payload = body.split(b'id="rings-data">', 1)[1].split(b"</script>", 1)[0]
        self.assertNotIn(b"</", payload)

    def test_charts_are_islands_not_svg(self):
        body = self._get("/hx/overview", headers={"HX-Request": "true"})
        self.assertIn(b'data-chart="stacked"', body)
        self.assertIn(b"data-opt=", body)

    def test_post_unknown_hx_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._post_form("/hx/nope", {})
        self.assertEqual(cm.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
