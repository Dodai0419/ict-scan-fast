"""Deterministic synthetic candles + timing harness.

  python bench.py gen 5000 7 > candles.json     # n candles, seed
  python bench.py time candles.json [scanner.py] # median of 3 runs of scan()
"""
import importlib.util
import json
import random
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone


def gen(n, seed):
    rnd = random.Random(seed)
    t0 = datetime(2026, 3, 1, tzinfo=timezone.utc)  # crosses the US DST switch on 2026-03-08
    price, rows = 100.0, []
    for i in range(n):
        o = price
        c = round(o + rnd.gauss(0, 1) * (3 if rnd.random() < 0.08 else 0.6), 2)
        h = round(max(o, c) + abs(rnd.gauss(0, 0.4)), 2)
        l = round(min(o, c) - abs(rnd.gauss(0, 0.4)), 2)
        rows.append({"timestamp": (t0 + timedelta(minutes=5 * i)).isoformat(),
                     "open": o, "high": h, "low": l, "close": c, "volume": 100 + i})
        price = c
    return {"meta": {"instrument": "SYN", "timeframe": "5m"}, "candles": rows}


def load(path):
    spec = importlib.util.spec_from_file_location("scanner", path)
    mod = sys.modules["scanner"] = importlib.util.module_from_spec(spec)  # dataclasses need it registered
    spec.loader.exec_module(mod)
    return mod


if __name__ == "__main__":
    if sys.argv[1] == "gen":
        json.dump(gen(int(sys.argv[2]), int(sys.argv[3])), sys.stdout)
    else:
        payload = json.load(open(sys.argv[2]))
        mod = load(sys.argv[3] if len(sys.argv) > 3 else "ict_scan.py")
        times = []
        for _ in range(3):
            t = time.perf_counter()
            candles, meta = mod.parse_candles(payload)
            mod.scan(candles, meta)
            times.append(time.perf_counter() - t)
        print(f"{len(payload['candles'])} candles: {statistics.median(times) * 1000:.1f} ms (median of 3)")
