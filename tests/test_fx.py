"""USD→BRL rate helper."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from token_dashboard.fx import DEFAULT_USD_BRL, usd_brl_rate, usd_to_brl


class TestFx(unittest.TestCase):
    def test_default_when_missing(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "missing"
            # clear env override if present
            old = os.environ.pop("USD_BRL", None)
            try:
                self.assertEqual(usd_brl_rate(p), DEFAULT_USD_BRL)
            finally:
                if old is not None:
                    os.environ["USD_BRL"] = old

    def test_file_rate(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "rate"
            p.write_text("5.5\n", encoding="utf-8")
            old = os.environ.pop("USD_BRL", None)
            try:
                self.assertEqual(usd_brl_rate(p), 5.5)
                self.assertEqual(usd_to_brl(2.0, rate=5.5), 11.0)
            finally:
                if old is not None:
                    os.environ["USD_BRL"] = old

    def test_env_override(self):
        old = os.environ.get("USD_BRL")
        os.environ["USD_BRL"] = "6.0"
        try:
            self.assertEqual(usd_brl_rate(), 6.0)
        finally:
            if old is None:
                os.environ.pop("USD_BRL", None)
            else:
                os.environ["USD_BRL"] = old


if __name__ == "__main__":
    unittest.main()
