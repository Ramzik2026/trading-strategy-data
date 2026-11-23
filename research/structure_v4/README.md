# FVG 1W trend + 1H BOS: independent annual study (v4)

Only uses **public BingX market data**. No exchange keys, private endpoints, orders, account actions, notifications or changes to the live Telegram bot.

## Pre-registered methods (do not adjust using the holdout data)

Study: BTC-USDT, ETH-USDT, SOL-USDT USDT perpetual, 365 days of completed 4H and 1H candles, no gaps, cross-timeframe OHLC consistency verified.

Signals: three-candle FVG on 4H, width >0.1% of lower boundary. First 1H contact within 48 hours of formation.

Four variants share the same candidate zones:
- Baseline: at first 1H contact, execution approximated using next 1H open; if that first hour crosses stop, count -1R and costs rather than discarding loss.
- Weekly: baseline, only if side matches weekly trend known at formation.
- 1H BOS: after first zone contact, require directional close crossing latest confirmed swing (two bars on each side; not older than 48 hours). Must occur no later than 12 hours after first contact and before stop; execute next 1H open.
- Both: weekly alignment and 1H BOS.

Weekly trend: only fully completed Monday–Sunday UTC weeks (42 closed 4H candles). Last completed weekly close > SMA8 and > previous weekly close gives bullish; both below gives bearish; else neutral.

Trade: stop 0.05% beyond far FVG edge, target 2x risk from actual entry, maximum 48 closed 1H candles; stop first if one 1H candle contains both TP and SL. Round-trip hypothetical friction **14 basis points of entry notional** converted to R. Not a verified BingX fee. No funding, liquidation, real spread, tick sequence, position overlap constraints or portfolio sizing.

For each asset, first 60% of chronological bar sequence is development period, last 40% holdout; cannot tune the above conditions based on holdout outcomes.

Run from repository root:
```
python -m unittest discover -s research/structure_v4/tests -v
python research/structure_v4/structure.py --days 365 --out research-structure-output
```

Research results are saved as JSON (including each hypothetical signal and its four outcomes) and Markdown. This is **not** a live trading or ROI claim.

The workflow is isolated to branch `research/fvg-structure-1w-1h-v4`, with read-only GitHub token, zero secrets, no connection to the operational scanner.
