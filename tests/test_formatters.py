import os
import time
import unittest

from token_dashboard.hx_views import ts


class TimestampDisplayTests(unittest.TestCase):
    def setUp(self):
        self._tz = os.environ.get("TZ")
        os.environ["TZ"] = "America/Sao_Paulo"
        time.tzset()

    def tearDown(self):
        if self._tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._tz
        time.tzset()

    def test_utc_iso_renders_local_wall_clock(self):
        # 21:07 UTC → 18:07 in São Paulo (UTC-3)
        self.assertEqual(ts("2026-09-01T21:07:52+00:00"), "2026-09-01 18:07")

    def test_zulu_suffix(self):
        self.assertEqual(ts("2026-09-01T21:01:37Z"), "2026-09-01 18:01")

    def test_empty(self):
        self.assertEqual(ts(None), "")
        self.assertEqual(ts(""), "")
