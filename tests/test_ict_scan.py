import json
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ict_scan  # noqa: E402
from ict_scan import detect_fvgs, parse_candles, scan  # noqa: E402

GOLDEN = ROOT / "tests" / "golden"


def run_cli(*args):
    out = subprocess.run([sys.executable, str(ROOT / "ict_scan.py"), *args], capture_output=True, text=True)
    return out.returncode, out.stdout, out.stderr


class SameOutputAsUpstream(unittest.TestCase):
    """Golden files were written by carreypro/ict-trading-model scripts/ict_scan.py (with tzdata)."""

    def check(self, name, *flags):
        code, out, err = run_cli("--input", str(GOLDEN / f"in_{name[0]}.json"), *flags)
        self.assertEqual(code, 0, err)
        self.assertEqual(out, (GOLDEN / f"out_{name}.json").read_text(encoding="utf-8"))

    def test_seed_1(self):
        self.check("1")

    def test_seed_2(self):
        self.check("2")

    def test_seed_3(self):
        self.check("3")

    def test_short_wide_swings_low_rr(self):
        self.check("1_short", "--direction", "short", "--swing-width", "3", "--minimum-rr", "1.5")


class NewYorkFallback(unittest.TestCase):
    def test_rule_based_offsets_match_known_dst_switches(self):
        utc = timezone.utc
        cases = {
            datetime(2026, 3, 8, 6, 59, tzinfo=utc): "-05:00",   # 01:59 EST
            datetime(2026, 3, 8, 7, 0, tzinfo=utc): "-04:00",    # 03:00 EDT
            datetime(2026, 11, 1, 5, 59, tzinfo=utc): "-04:00",  # 01:59 EDT
            datetime(2026, 11, 1, 6, 0, tzinfo=utc): "-05:00",   # 01:00 EST
            datetime(2024, 7, 4, 12, 0, tzinfo=utc): "-04:00",
            datetime(2025, 1, 1, 3, 0, tzinfo=utc): "-05:00",
        }
        for moment, offset in cases.items():
            self.assertTrue(ict_scan._ny_rules(moment).isoformat().endswith(offset), moment)


def bullish_fixture():
    base = datetime(2026, 10, 9, 13, 30, tzinfo=timezone.utc)
    values = [(101, 103, 99, 102), (102, 115, 100, 110), (110, 111, 98, 100), (100, 103, 99, 102),
              (102, 105, 100, 104), (104, 104.5, 99, 100), (100, 101, 97, 99), (99, 104, 98.8, 103.8),
              (103.8, 110, 103, 106), (106, 107, 101.5, 103)]
    return {"meta": {"instrument": "TEST-USDT", "timeframe": "5m"},
            "candles": [{"timestamp": (base + timedelta(minutes=i * 5)).isoformat(), "open": r[0], "high": r[1],
                         "low": r[2], "close": r[3], "volume": 100 + i} for i, r in enumerate(values)]}


class PortedUpstreamTests(unittest.TestCase):
    def run_fixture(self, payload=None):
        candles, meta = parse_candles(payload or bullish_fixture())
        return scan(candles, meta, swing_width=1, setup_lookahead=5, displacement_body_multiple=1.2,
                    displacement_atr_multiple=0.5, minimum_rr=2.0, direction="long")

    def test_bullish_fvg_zone_and_ce(self):
        candles, _ = parse_candles(bullish_fixture())
        gap = next(g for g in detect_fvgs(candles) if g["middle_index"] == 7)
        self.assertEqual((gap["direction"], gap["zone"], gap["ce"]), ("bullish", [101.0, 103.0], 102.0))

    def test_complete_bullish_sequence_confirms(self):
        c = self.run_fixture()["latest_candidate"]
        self.assertEqual(c["mechanical_status"], "confirmed")
        self.assertEqual(c["sweep"]["session"], "silver_bullet")
        self.assertEqual(c["target"]["price"], 115.0)

    def test_wick_without_close_does_not_confirm_mss(self):
        payload = bullish_fixture()
        payload["candles"][8]["close"] = 104
        c = self.run_fixture(payload)["latest_candidate"]
        self.assertEqual(c["mechanical_status"], "developing")
        self.assertFalse(c["checks"]["closed_candle_mss"])

    def test_okx_newest_first_arrays_are_sorted(self):
        rows = [[int(datetime.fromisoformat(r["timestamp"]).timestamp() * 1000), r["open"], r["high"], r["low"],
                 r["close"], r["volume"]] for r in reversed(bullish_fixture()["candles"])]
        candles, _ = parse_candles(rows)
        self.assertLess(candles[0].timestamp, candles[-1].timestamp)

    def test_bad_input_exits_2(self):
        code, _, err = run_cli("--input", str(ROOT / "README.md"))
        self.assertEqual(code, 2)
        self.assertIn("error:", err)


if __name__ == "__main__":
    unittest.main()
