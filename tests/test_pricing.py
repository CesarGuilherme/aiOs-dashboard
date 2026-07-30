import os
import unittest

from token_dashboard.pricing import load_pricing, cost_for, format_for_user

PRICING = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "pricing.json"))


class CostTests(unittest.TestCase):
    def setUp(self):
        self.p = load_pricing(PRICING)

    def _u(self, **kw):
        base = {
            "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
            "cache_create_5m_tokens": 0, "cache_create_1h_tokens": 0,
        }
        base.update(kw)
        return base

    def test_known_opus_input_cost(self):
        c = cost_for("claude-opus-4-7", self._u(input_tokens=1_000_000), self.p)
        self.assertAlmostEqual(c["usd"], 5.00, places=4)
        self.assertFalse(c["estimated"])

    def test_known_sonnet_output_cost(self):
        c = cost_for("claude-sonnet-4-6", self._u(output_tokens=1_000_000), self.p)
        self.assertAlmostEqual(c["usd"], 15.00, places=4)

    def test_unknown_opus_falls_back(self):
        c = cost_for("claude-opus-9-9-experimental", self._u(input_tokens=1_000_000), self.p)
        self.assertAlmostEqual(c["usd"], 5.00, places=4)
        self.assertTrue(c["estimated"])

    def test_unknown_unparseable_returns_none(self):
        c = cost_for("custom-local-model", self._u(input_tokens=9999), self.p)
        self.assertIsNone(c["usd"])

    def test_fable_and_sonnet5_rates(self):
        c = cost_for("claude-fable-5", self._u(input_tokens=1_000_000), self.p)
        self.assertAlmostEqual(c["usd"], 10.00, places=4)
        self.assertFalse(c["estimated"])
        c = cost_for("claude-sonnet-5", self._u(input_tokens=1_000_000, output_tokens=1_000_000), self.p)
        self.assertAlmostEqual(c["usd"], 12.00, places=4)  # intro $2+$10 through 2026-08-31
        c = cost_for("claude-opus-5", self._u(input_tokens=1_000_000), self.p)
        self.assertAlmostEqual(c["usd"], 5.00, places=4)
        c = cost_for("claude-opus-4-8", self._u(input_tokens=1_000_000), self.p)
        self.assertAlmostEqual(c["usd"], 5.00, places=4)

    def test_mythos_falls_back_to_fable_tier(self):
        c = cost_for("claude-mythos-future-9", self._u(input_tokens=1_000_000), self.p)
        self.assertAlmostEqual(c["usd"], 10.00, places=4)
        self.assertTrue(c["estimated"])

    def test_cache_read_cheaper_than_input(self):
        c_in = cost_for("claude-opus-4-7", self._u(input_tokens=1_000_000), self.p)
        c_cr = cost_for("claude-opus-4-7", self._u(cache_read_tokens=1_000_000), self.p)
        self.assertLess(c_cr["usd"], c_in["usd"])


class PlanFormatTests(unittest.TestCase):
    def setUp(self):
        self.p = load_pricing(PRICING)

    def test_api_plan_returns_raw(self):
        out = format_for_user(12.34, "api", self.p)
        self.assertEqual(out["display_usd"], 12.34)
        self.assertIsNone(out["subscription_usd"])

    def test_pro_plan_returns_subscription_subtitle(self):
        out = format_for_user(12.34, "pro", self.p)
        self.assertEqual(out["subscription_usd"], 20)
        self.assertIn("Pro", out["subtitle"])


class PricingPageModelsTests(unittest.TestCase):
    """Settings table allowlist must reference real rate rows."""

    def setUp(self):
        self.p = load_pricing(PRICING)

    def test_pricing_page_models_subset_of_models(self):
        page = self.p.get("pricing_page_models") or []
        self.assertTrue(page, "pricing_page_models must list Claude Code + Grok Build models")
        missing = [m for m in page if m not in self.p["models"]]
        self.assertEqual(missing, [], f"unknown models in pricing_page_models: {missing}")

    def test_pricing_page_covers_claude_and_grok(self):
        page = set(self.p.get("pricing_page_models") or [])
        self.assertTrue(any(m.startswith("claude-") for m in page))
        self.assertTrue(any(m.startswith("grok-") for m in page))
        self.assertIn("grok-4.5", page)
        self.assertIn("claude-opus-5", page)
        self.assertIn("claude-opus-4-8", page)
        # Page is a subset — legacy/rare IDs stay in models for billing only.
        self.assertNotIn("claude-opus-4-1", page)
        self.assertLess(len(page), len(self.p["models"]))


if __name__ == "__main__":
    unittest.main()
