#!/usr/bin/env python3
"""Scan OHLCV candles for strict ICT setup evidence. Same JSON output as
carreypro/ict-trading-model scripts/ict_scan.py, in near-linear time and without tzdata.

Input: a JSON candle array or {"candles": [...], "meta": {...}}. Candles are objects with
timestamp/open/high/low/close/volume fields or OKX-style arrays [ts, o, h, l, c, vol, ...].
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from bisect import bisect_left, bisect_right, insort
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path

Candle = namedtuple("Candle", "timestamp open high low close volume", defaults=(None,))
INF = float("inf")

try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
except Exception:  # Windows and slim images ship no tz database
    NY = None


def _ny_rules(moment: datetime) -> datetime:
    """New York time from the US DST rule (2nd Sunday of March 07:00 UTC to 1st Sunday of Nov 06:00 UTC).
    shortcut: rule in force since 2007; install tzdata for older candles."""
    y = moment.year
    march = datetime(y, 3, 8, 7, tzinfo=timezone.utc)
    nov = datetime(y, 11, 1, 6, tzinfo=timezone.utc)
    start = march + timedelta(days=(6 - march.weekday()) % 7)
    end = nov + timedelta(days=(6 - nov.weekday()) % 7)
    return moment.astimezone(timezone(timedelta(hours=-4 if start <= moment < end else -5)))


def ny(moment: datetime) -> datetime:
    return moment.astimezone(NY) if NY else _ny_rules(moment)


def _parse_timestamp(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
        return datetime.fromtimestamp(seconds / 1000.0 if seconds > 10_000_000_000 else seconds, tz=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if text.replace(".", "", 1).isdigit():
            return _parse_timestamp(float(text))
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    raise ValueError(f"unsupported timestamp: {value!r}")


def _float(value, label):
    if isinstance(value, bool):
        raise ValueError(f"{label} must be numeric")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc


def parse_candles(payload):
    rows, meta = (payload.get("candles"), payload.get("meta", {})) if isinstance(payload, dict) else (payload, {})
    if not isinstance(rows, list) or len(rows) < 10:
        raise ValueError("at least 10 candles are required")
    if not isinstance(meta, dict):
        raise ValueError("meta must be an object")
    candles = []
    for i, row in enumerate(rows):
        if isinstance(row, dict):
            ts = row.get("timestamp", row.get("ts", row.get("time")))
            values = [row.get(k, row.get(k[0])) for k in ("open", "high", "low", "close")]
            vol = row.get("volume", row.get("vol", row.get("v")))
        elif isinstance(row, (list, tuple)) and len(row) >= 5:
            ts, values, vol = row[0], row[1:5], (row[5] if len(row) > 5 else None)
        else:
            raise ValueError(f"candle {i} has an unsupported shape")
        ts = _parse_timestamp(ts)  # before the prices, so errors are reported in upstream's order
        o, h, l, c = (_float(v, f"candle {i} {k}") for v, k in zip(values, ("open", "high", "low", "close")))
        candle = Candle(ts, o, h, l, c,
                        _float(vol, f"candle {i} volume") if vol is not None else None)
        if l > min(o, c) or h < max(o, c):
            raise ValueError(f"candle {i} OHLC values are inconsistent")
        if h < l:
            raise ValueError(f"candle {i} high is below low")
        candles.append(candle)
    candles.sort(key=lambda c: c.timestamp)
    if len({c.timestamp for c in candles}) != len(candles):
        raise ValueError("candle timestamps must be unique")
    return candles, meta


def iso(candle):
    return candle.timestamp.isoformat().replace("+00:00", "Z")


def detect_swings(candles, width):
    if width < 1:
        raise ValueError("swing width must be at least 1")
    swings = []
    for i in range(width, len(candles) - width):
        c, around = candles[i], candles[i - width:i] + candles[i + 1:i + width + 1]
        if c.high > max(o.high for o in around):
            swings.append({"kind": "high", "index": i, "price": c.high, "time": iso(c)})
        if c.low < min(o.low for o in around):
            swings.append({"kind": "low", "index": i, "price": c.low, "time": iso(c)})
    return swings


def detect_fvgs(candles):
    gaps = []
    for end in range(2, len(candles)):
        first, third = candles[end - 2], candles[end]
        if first.high < third.low:
            lower, upper, direction = first.high, third.low, "bullish"
        elif first.low > third.high:
            lower, upper, direction = third.high, first.low, "bearish"
        else:
            continue
        gaps.append({"direction": direction, "start_index": end - 2, "middle_index": end - 1, "end_index": end,
                     "time": iso(candles[end - 1]), "zone": [lower, upper], "ce": (lower + upper) / 2.0,
                     "size": upper - lower})
    return gaps


def atr_at(candles, index, period):
    values = [max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))
              for p, c in zip(candles[max(0, index - period):index], candles[max(1, index - period + 1):index + 1])]
    return sum(values) / len(values) if values else None


def _median_body_before(candles, index, window=20):
    bodies = [abs(c.close - c.open) for c in candles[max(0, index - window):index]]
    nonzero = [b for b in bodies if b > 0]
    return statistics.median(nonzero) if nonzero else 0.0


def _session_name(local):
    minute = local.hour * 60 + local.minute
    for lo, hi, name in ((120, 300, "london_focus"), (420, 600, "new_york_open"), (600, 660, "silver_bullet"),
                         (720, 810, "lunch_no_trade"), (810, 960, "afternoon_conditional")):
        if lo <= minute < hi:
            return name
    return "outside_preferred_window"


def detect_sweeps(candles, swings, lookback):
    """One pass: the reference is the latest swing of each kind strictly before the candle."""
    sweeps, last, j = [], {"low": None, "high": None}, 0
    for i, c in enumerate(candles):
        while j < len(swings) and swings[j]["index"] < i:
            last[swings[j]["kind"]] = swings[j]
            j += 1
        for kind, direction, name, extreme in (("low", "long", "sellside_sweep", c.low),
                                               ("high", "short", "buyside_sweep", c.high)):
            ref = last[kind]
            if ref is None or i - ref["index"] > lookback:
                continue
            swept = (c.low < ref["price"] < c.close) if kind == "low" else (c.high > ref["price"] > c.close)
            if swept:
                local = ny(c.timestamp)
                sweeps.append({"direction": direction, "kind": name, "index": i, "time": iso(c),
                               "ny_time": local.isoformat(), "session": _session_name(local),
                               "extreme": extreme, "reference": ref})
    return sweeps


def build_candidate(candles, by_kind, ranked, fvgs, fvg_mid, sweep, *, lookahead, atr_period,
                    displacement_body_multiple, displacement_atr_multiple, minimum_rr):
    direction, si = sweep["direction"], sweep["index"]
    long = direction == "long"
    opposite = by_kind["high" if long else "low"]
    k = bisect_left(opposite[0], si)
    mss_reference = opposite[1][k - 1] if k else None
    end = min(len(candles) - 1, si + lookahead)

    mss_index = None
    if mss_reference:
        for i in range(si + 1, end + 1):
            if (candles[i].close > mss_reference["price"]) if long else (candles[i].close < mss_reference["price"]):
                mss_index = i
                break

    fvg, desired = None, "bullish" if long else "bearish"
    for gap in fvgs[bisect_left(fvg_mid, si):]:  # sorted by end_index, so the first match is the earliest
        if gap["end_index"] > end:
            break
        if gap["direction"] != desired:
            continue
        m = candles[gap["middle_index"]]
        median_body = _median_body_before(candles, gap["middle_index"])
        atr = atr_at(candles, gap["middle_index"], atr_period)
        body, span = abs(m.close - m.open), m.high - m.low
        if (median_body > 0 and body >= median_body * displacement_body_multiple
                and atr is not None and span >= atr * displacement_atr_multiple
                and (m.close > m.open if long else m.close < m.open)):
            fvg = {**gap, "body_multiple": body / median_body, "range_atr": span / atr}
            break

    range_end = mss_index if mss_index is not None else (fvg["end_index"] if fvg else si)
    segment = candles[si:range_end + 1]
    range_low, range_high = min(c.low for c in segment), max(c.high for c in segment)
    equilibrium = (range_low + range_high) / 2.0
    width = range_high - range_low
    ote = [range_high - 0.79 * width, range_high - 0.62 * width] if long else \
        [range_low + 0.62 * width, range_low + 0.79 * width]

    entry = fvg["ce"] if fvg else None
    location_ok = (entry <= equilibrium if long else entry >= equilibrium) if fvg else None
    ote_overlap = max(fvg["zone"][0], ote[0]) <= min(fvg["zone"][1], ote[1]) if fvg else None

    # Target: nearest earlier swing beyond the range; ties go to the earliest swing, as upstream's min/max did.
    target, r = None, ranked["high" if long else "low"]
    if entry is not None and long:
        i = bisect_right(r, (range_high, INF))
        target = r[i][2] if i < len(r) else None
    elif entry is not None:
        i = bisect_left(r, (range_low, -1))
        target = r[bisect_left(r, (r[i - 1][0], -1))][2] if i else None
    stop, rr = sweep["extreme"], None
    if entry is not None and target:
        risk = abs(entry - stop)
        if risk > 0:
            rr = (target["price"] - entry if long else entry - target["price"]) / risk

    retest_index = invalidation_index = None
    target_before_retest = False
    if fvg:
        start = max(si + 1, (mss_index + 1) if mss_index is not None else si + 1, fvg["end_index"] + 1)
        for i in range(start, len(candles)):
            c = candles[i]
            touched = c.low <= fvg["zone"][1] and c.high >= fvg["zone"][0]
            if c.low < stop if long else c.high > stop:
                invalidation_index = i
                break
            if target and (c.high >= target["price"] if long else c.low <= target["price"]) and not touched:
                target_before_retest = True
                break
            if touched:
                retest_index = i
                break

    checks = {"meaningful_sweep": True, "displacement_and_strict_fvg": fvg is not None,
              "closed_candle_mss": mss_index is not None, "correct_premium_discount_side": location_ok,
              "retrace_observed": retest_index is not None, "minimum_rr": rr is not None and rr >= minimum_rr}
    if invalidation_index is not None:
        status, reason = "invalidated", "structural sweep extreme was crossed before a valid retest"
    elif target_before_retest:
        status, reason = "no-trade", "candidate target was reached before the retrace; entry is late"
    elif location_ok is False:
        status, reason = "no-trade", "entry FVG is on the wrong side of equilibrium"
    elif rr is not None and rr < minimum_rr:
        status, reason = "no-trade", f"candidate R:R {rr:.2f} is below {minimum_rr:.2f}"
    elif all(checks.values()):
        status, reason = "confirmed", "all mechanical ICT setup gates passed on closed candles"
    else:
        status = "developing"
        reason = "waiting for: " + ", ".join(n for n, v in checks.items() if v is not True)

    at = lambda i: iso(candles[i]) if i is not None else None  # noqa: E731
    return {
        "mechanical_status": status, "final_status_requires_manual_context": True, "reason": reason,
        "direction": direction, "sweep": sweep, "displacement_fvg": fvg,
        "mss": {"reference": mss_reference, "break_index": mss_index, "break_time": at(mss_index),
                "break_close": candles[mss_index].close if mss_index is not None else None},
        "dealing_range": {"low": range_low, "high": range_high, "equilibrium": equilibrium, "ote_zone": ote,
                          "fvg_ote_overlap": ote_overlap},
        "entry": {"zone": fvg["zone"] if fvg else None, "ce": entry, "retest_index": retest_index,
                  "retest_time": at(retest_index)},
        "invalidation": {"structural_level": stop,
                         "instruction": "place any operational buffer beyond this level using venue tick/volatility data",
                         "crossed_index": invalidation_index, "crossed_time": at(invalidation_index)},
        "target": target, "gross_rr_to_candidate_target": rr, "minimum_rr": minimum_rr, "checks": checks,
    }


def scan(candles, meta, *, swing_width=2, sweep_lookback=80, setup_lookahead=12, atr_period=14,
         displacement_body_multiple=1.5, displacement_atr_multiple=1.0, minimum_rr=2.0, direction="auto"):
    if direction not in {"auto", "long", "short"}:
        raise ValueError("direction must be auto, long, or short")
    swings = detect_swings(candles, swing_width)
    fvgs = detect_fvgs(candles)
    sweeps = [s for s in detect_sweeps(candles, swings, sweep_lookback) if direction in ("auto", s["direction"])]
    by_kind = {}
    for kind in ("high", "low"):
        same = [s for s in swings if s["kind"] == kind]
        by_kind[kind] = ([s["index"] for s in same], same)
    fvg_mid = [g["middle_index"] for g in fvgs]
    params = dict(lookahead=setup_lookahead, atr_period=atr_period, minimum_rr=minimum_rr,
                  displacement_body_multiple=displacement_body_multiple,
                  displacement_atr_multiple=displacement_atr_multiple)
    # Swings before each sweep, kept sorted by (price, order) so the target lookup is a bisect.
    ranked, candidates, j = {"high": [], "low": []}, [], 0
    for s in sweeps:
        while j < len(swings) and swings[j]["index"] < s["index"]:
            insort(ranked[swings[j]["kind"]], (swings[j]["price"], j, swings[j]))
            j += 1
        candidates.append(build_candidate(candles, by_kind, ranked, fvgs, fvg_mid, s, **params))
    return {
        "meta": {**meta, "candle_count": len(candles), "first_candle": iso(candles[0]),
                 "last_candle": iso(candles[-1]), "timezone_for_sessions": "America/New_York"},
        "parameters": {"swing_width": swing_width, "sweep_lookback": sweep_lookback,
                       "setup_lookahead": setup_lookahead, "atr_period": atr_period,
                       "displacement_body_multiple": displacement_body_multiple,
                       "displacement_atr_multiple": displacement_atr_multiple, "minimum_rr": minimum_rr,
                       "direction": direction},
        "counts": {"swings": len(swings), "strict_fvgs": len(fvgs), "liquidity_sweeps": len(sweeps),
                   "candidates": len(candidates)},
        "latest_candidate": candidates[-1] if candidates else None,
        "candidates": candidates[-20:],
        "recent_swings": swings[-30:],
        "recent_fvgs": fvgs[-30:],
        "warnings": [
            "Mechanical detections require manual HTF narrative and meaningful-level validation.",
            "A mechanical confirmed result is not a final confirmed trade setup until context is verified.",
            "R:R is gross and excludes fees, spread, slippage, and contract specifications.",
            "ICT labels are price-action hypotheses, not proof of institutional activity.",
        ],
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True, help="JSON path, or - for stdin")
    p.add_argument("--output", help="Optional output JSON path")
    p.add_argument("--direction", choices=("auto", "long", "short"), default="auto")
    p.add_argument("--swing-width", type=int, default=2)
    p.add_argument("--sweep-lookback", type=int, default=80)
    p.add_argument("--setup-lookahead", type=int, default=12)
    p.add_argument("--atr-period", type=int, default=14)
    p.add_argument("--body-multiple", type=float, default=1.5)
    p.add_argument("--atr-multiple", type=float, default=1.0)
    p.add_argument("--minimum-rr", type=float, default=2.0)
    a = p.parse_args()
    try:
        raw = sys.stdin.read() if a.input == "-" else Path(a.input).read_text(encoding="utf-8")
        candles, meta = parse_candles(json.loads(raw))
        result = scan(candles, meta, swing_width=a.swing_width, sweep_lookback=a.sweep_lookback,
                      setup_lookahead=a.setup_lookahead, atr_period=a.atr_period,
                      displacement_body_multiple=a.body_multiple, displacement_atr_multiple=a.atr_multiple,
                      minimum_rr=a.minimum_rr, direction=a.direction)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if a.output:
        Path(a.output).write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
