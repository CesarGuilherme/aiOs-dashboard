"""Offline tests for the xAI pricing-page sync. No network."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from urllib.error import URLError

from token_dashboard.db import connect, init_db
from token_dashboard.pricing_sync import merge_grok_pricing, parse_text_api_table, sync_pricing

FIXTURE = """
### Text API Pricing

| Model | Context | Input / 1M tokens | Cached input / 1M tokens | Output / 1M tokens |
| --- | --- | --- | --- | --- |
| grok-4.8 (< 200k prompt tokens) | 500k | $2.00 | $0.50 | $6.00 |
| grok-4.8 (≥ 200k prompt tokens) | 500k | $4.00 | $1.00 | $12.00 |
| grok-4.7 (< 200k prompt tokens) | 500k | $2.00 | $0.50 | $6.00 |
| grok-4.7 (≥ 200k prompt tokens) | 500k | $4.00 | $1.00 | $12.00 |
| grok-4.5 (< 200k prompt tokens) | 500k | $2.00 | $0.30 | $6.00 |
| grok-4.5 (≥ 200k prompt tokens) | 500k | $4.00 | $0.60 | $12.00 |
| grok-build-0.1 (< 200k prompt tokens) | 256k | $1.00 | $0.20 | $2.00 |
| grok-build-0.1 (≥ 200k prompt tokens) | 256k | $2.00 | $0.40 | $4.00 |

### Imagine Pricing

| Model | Cost |
| --- | --- |
| grok-imagine-image | $0.02 / image |
"""


def _pricing():
    return {
        "models": {
            "claude-opus-4-7": {
                "tier": "opus", "input": 5.0, "output": 25.0, "cache_read": 0.5,
                "cache_create_5m": 6.25, "cache_create_1h": 10.0,
            },
            "grok-4.5": {
                "tier": "grok", "input": 2.0, "output": 6.0, "cache_read": 0.5,
                "cache_create_5m": 2.0, "cache_create_1h": 2.0,
            },
            "grok-composer-2.5-fast": {
                "tier": "grok", "input": 1.0, "output": 5.0, "cache_read": 0.25,
                "cache_create_5m": 1.25, "cache_create_1h": 1.25,
            },
            "grok-build": {
                "tier": "grok", "input": 1.0, "output": 2.0, "cache_read": 0.2,
                "cache_create_5m": 1.0, "cache_create_1h": 1.0,
            },
        },
        "pricing_page_models": [
            "claude-opus-4-7", "grok-4.5", "grok-build", "grok-composer-2.5-fast",
        ],
        "tier_fallback": {
            "grok": {
                "tier": "grok", "input": 2.0, "output": 6.0, "cache_read": 0.5,
                "cache_create_5m": 2.0, "cache_create_1h": 2.0,
            },
        },
        "plans": {"api": {"monthly": 0, "label": "API (pay-per-token)"}},
    }


class ParseTests(unittest.TestCase):
    def test_short_context_row_wins(self):
        parsed = parse_text_api_table(FIXTURE)
        self.assertEqual(parsed["grok-4.8"], {"input": 2.0, "output": 6.0, "cache_read": 0.5})
        self.assertEqual(parsed["grok-4.5"]["cache_read"], 0.3)
        self.assertNotIn("grok-imagine-image", parsed)


class MergeTests(unittest.TestCase):
    def test_adds_short_rate_and_fixes_cache_without_touching_others(self):
        pricing = _pricing()
        claude = dict(pricing["models"]["claude-opus-4-7"])
        composer = dict(pricing["models"]["grok-composer-2.5-fast"])
        changed = merge_grok_pricing(pricing, parse_text_api_table(FIXTURE), set())
        self.assertIn("grok-4.8", changed)
        row = pricing["models"]["grok-4.8"]
        self.assertEqual(row["input"], 2.0)
        self.assertEqual(row["output"], 6.0)
        self.assertEqual(row["cache_read"], 0.5)
        self.assertEqual(row["cache_create_5m"], 2.0)
        self.assertEqual(pricing["models"]["grok-4.5"]["cache_read"], 0.3)
        self.assertEqual(pricing["models"]["claude-opus-4-7"], claude)
        self.assertEqual(pricing["models"]["grok-composer-2.5-fast"], composer)
        self.assertEqual(pricing["models"]["grok-build"]["input"], 1.0)
        self.assertEqual(pricing["tier_fallback"]["grok"]["input"], 2.0)
        self.assertNotIn("grok-4.8", pricing["pricing_page_models"])
        self.assertEqual(pricing["plans"]["api"]["monthly"], 0)

    def test_page_lists_model_only_when_a_message_used_it(self):
        parsed = parse_text_api_table(FIXTURE)
        absent = _pricing()
        merge_grok_pricing(absent, parsed, set())
        self.assertNotIn("grok-4.8", absent["pricing_page_models"])
        present = _pricing()
        merge_grok_pricing(present, parsed, {"grok-4.8"})
        self.assertIn("grok-4.8", present["pricing_page_models"])


class SyncFileTests(unittest.TestCase):
    def test_fetch_error_keeps_file(self):
        pricing = _pricing()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "pricing.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(pricing, f)
            before = Path(path).read_text(encoding="utf-8")

            def boom():
                raise URLError("offline")

            line = sync_pricing(path, fetch=boom)
            self.assertEqual(line, "pricing: kept file (offline)")
            self.assertEqual(Path(path).read_text(encoding="utf-8"), before)

    def test_sync_writes_seen_model_onto_the_page(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "pricing.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(_pricing(), f)
            db = os.path.join(d, "t.db")
            init_db(db)
            with connect(db) as c:
                c.execute(
                    """INSERT INTO messages (
                        uuid, session_id, project_slug, type, timestamp, model, source
                    ) VALUES ('u1', 's1', 'p', 'assistant', '2026-09-22T00:00:00+00:00',
                              'grok-4.8', 'grok')"""
                )
                c.commit()
            line = sync_pricing(path, db_path=db, fetch=lambda: FIXTURE)
            self.assertIn("grok-4.8", line)
            saved = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertEqual(saved["models"]["grok-4.8"]["input"], 2.0)
            self.assertEqual(saved["models"]["grok-4.5"]["cache_read"], 0.3)
            self.assertIn("grok-4.8", saved["pricing_page_models"])
            self.assertEqual(saved["models"]["claude-opus-4-7"]["tier"], "opus")
            self.assertEqual(saved["models"]["grok-composer-2.5-fast"]["output"], 5.0)


if __name__ == "__main__":
    unittest.main()
