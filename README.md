<div align="center">

# ict-scan-fast

**Scan OHLCV candles for ICT setups in near-linear time, byte-identical to ict-trading-model's scanner.**

A drop-in rewrite of the scanner in [carreypro/ict-trading-model](https://github.com/carreypro/ict-trading-model): same JSON, 6-18x faster on 10k candles, and it runs on Windows without `tzdata`.

![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg) ![Status: prototype](https://img.shields.io/badge/status-prototype-orange.svg)

Works with **Claude Code** and **Codex**.

</div>

## Quick start
```bash
git clone https://github.com/Dodai0419/ict-scan-fast && cd ict-scan-fast
python ict_scan.py --input tests/golden/in_1.json
```
```text
{
  "meta": {"instrument": "SYN", "timeframe": "5m", "candle_count": 400, ...},
  "counts": {"swings": 109, "strict_fvgs": 72, "liquidity_sweeps": 49, "candidates": 49},
  "latest_candidate": {"mechanical_status": "invalidated",
                       "reason": "structural sweep extreme was crossed before a valid retest", ...}
}
```
(output trimmed; the real output is indented JSON)

## The problem
- The original scan rechecks every swing for every candle, so its run time grows with the square of the candle count: 0.4-0.6 s for 2,000 candles, 6-11 s for 10,000.
- On Windows (and slim Docker images) Python has no time zone database, so the original crashes with `ZoneInfoNotFoundError: America/New_York` unless you `pip install tzdata`.
- 557 lines for one scanner.

## The solution
| Idea | Fixes |
|------|-------|
| **One pass for sweeps**: keep the latest swing high/low while walking the candles | the O(candles x swings) loop |
| **Bisect instead of filtering**: MSS reference, first displacement FVG and the target come from sorted lists | per-sweep rescans of every swing and FVG |
| **Rule-based New York time when tzdata is missing** (US DST rule since 2007) | the Windows crash; with tzdata installed it uses `zoneinfo` like the original |
| **Golden tests against the original's output** | proof that nothing changed: `tests/golden/out_*.json` were written by the original script |

Same CLI flags, same JSON keys and values, same error messages, exit code 2 on bad input.

## Use with Claude Code
```bash
/plugin marketplace add Dodai0419/ict-scan-fast
/plugin install ict-scan-fast@ict-scan-fast
```

## Use with Codex (ChatGPT)
```bash
git clone https://github.com/Dodai0419/ict-scan-fast && cp -r ict-scan-fast/skills/* ~/.codex/skills/
```
Or copy `AGENTS.md` into your project root.

## Benchmarks
Synthetic 5-minute random-walk candles from `python bench.py gen N 7`, timed with `python bench.py time <file> <scanner>` (parse + scan, median of 3).
Windows 11, Python 3.11.1, the original run in a venv with `tzdata`. Two separate sessions; the machine was noisy, so both are shown.

| | original | this repo |
|-|-|-|
| 2,000 candles | 401 ms / 589 ms | 150 ms / 136 ms |
| 10,000 candles | 6,074 ms / 10,928 ms | 938 ms / 580 ms |
| 50,000 candles | not measured (quadratic, minutes) | 4,338 ms |
| lines of code (scanner) | 557 | 358 |
| dependencies | stdlib + tzdata on Windows | stdlib |

Same output: identical bytes to the original on 7 inputs x 3 flag sets (incl. an integer-price input full of ties), plus the 4 golden files in `tests/`.

## Inspired by
- [carreypro/ict-trading-model](https://github.com/carreypro/ict-trading-model) - the ICT model, scanner logic, output format and tests (MIT).

## Roadmap
- [ ] Stream candles from stdin line by line for very large files
- [ ] Optional multi-timeframe input in one call

## License
MIT. Analysis only; not financial advice. R:R is gross and excludes fees, spread and slippage.
