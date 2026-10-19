# AGENTS.md - ict-scan-fast


Mechanical ICT setup scan over closed candles. Analysis only: it never places orders,
and a mechanical `confirmed` still needs a human check of higher-timeframe context.

## Run
```bash
python ict_scan.py --input candles.json            # or --input - to read stdin
python ict_scan.py --input candles.json --direction long --minimum-rr 2.5 --output scan.json
```
Input: `[{"timestamp","open","high","low","close","volume"}, ...]`, `{"candles":[...],"meta":{...}}`,
or OKX arrays `[ts_ms, o, h, l, c, vol]` (any order; sorted by time). At least 10 candles.

## Read the result
- `latest_candidate.mechanical_status`: `confirmed | developing | invalidated | no-trade`, with `reason`.
- `checks`: which gates passed (sweep, displacement + strict FVG, closed-candle MSS, premium/discount side, retrace, minimum R:R).
- `entry.ce`, `invalidation.structural_level`, `target.price`, `gross_rr_to_candidate_target`.
- Always repeat the `warnings` to the user: R:R is gross, labels are hypotheses.

Options: `--swing-width 2 --sweep-lookback 80 --setup-lookahead 12 --atr-period 14 --body-multiple 1.5 --atr-multiple 1.0 --minimum-rr 2.0`.
