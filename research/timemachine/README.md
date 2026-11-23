# BingX FVG TimeMachine — isolated research v3

Read-only, public BingX USDT perpetual 4H candle research. Never accesses trading secrets, never sends orders or Telegram messages. Never changes active scanner.

The dedicated GitHub Actions workflow in branch research/fvg-timemachine-v3 retrieves 365 days of CLOSED 4H candles for BTC/ETH/SOL, checks continuity, evaluates historical FVG signals and writes both a Markdown report and JSON artifacts.

Locally: python -m unittest discover -s research/timemachine/tests -v
Then: python research/timemachine/annual.py --days 365 --draws 1000

Study assumptions:
- FVG on three CLOSED 4H candles, strictly >0.1% of lower boundary.
- Entry at near gap edge on first overlap within next 12 bars, with SL at far gap boundary ±0.05%, TP 2R, holding window 12 bars.
- Ambiguous candles count stop first; TP never awarded on entry candle.
- 270+ candles before signal and 1000 block-bootstrap synthetic paths (blocks of 4 bars). No future candles affect gate.
- Pre-registered gate: synthetic touch rate >=0.20 AND conditional modeled net-R > 0.
- Chronological 60% development / 40% holdout. Hypothetical total fees+slippage 14 basis points; actual BingX fees, funding, margin, tick ordering and liquidation NOT modeled.
- Individual signal statistics are NOT portfolio P&L, as trades may overlap.

Do not use this as evidence of actual profitability or a live-trading trigger.
