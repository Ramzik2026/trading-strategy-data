# Trading Strategy — independent Bybit market snapshots

Public, read-only market-data collection for independent paper research. **Not connected to AI Trading Platform.** No API keys, order placement, or live trading.

- GitHub Actions runs hourly at minute 12 UTC (GitHub scheduling can be delayed).
- Ten Bybit USDT linear perpetual symbols; closed 15m, 1h, 4h candles (up to 200 each).
- Also queries instrument Trading status, ticker (bid/ask, funding), open interest and recent funding history.
- The single public snapshot is `data/latest.json`. Check `collected_at_utc`, each endpoint's `server_time_ms`, `status` and `errors` before analysis.
- API errors are recorded, not interpreted as 'no setup'. Failed all-source runs do not overwrite previous snapshots.
- Data may be stale, delayed, blocked or incomplete. This is a collector, **not a signal engine**.

To run manually: GitHub → Actions → Bybit public market snapshot → Run workflow.
To inspect latest: https://raw.githubusercontent.com/Ramzik2026/trading-strategy-data/main/data/latest.json

The ChatGPT hourly automation must independently fetch and validate this URL; publishing here does not guarantee the ChatGPT environment can read it.
