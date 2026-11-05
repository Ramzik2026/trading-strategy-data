#!/usr/bin/env python3
"""Public Bybit USDT perpetual market snapshots. No credentials, no orders."""
import datetime as dt
import json
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "HYPEUSDT", "BNBUSDT", "DOGEUSDT", "LINKUSDT", "AVAXUSDT", "ADAUSDT")
INTERVALS = ("15", "60", "240")
HOSTS = ("https://api.bybit.com", "https://api.bytick.com")
ROOT = pathlib.Path("data")


def fetch(path, params):
    query = urllib.parse.urlencode(params)
    failures = []
    for host in HOSTS:
        url = host + path + "?" + query
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "MarketResearchSnapshot/1.0", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=12) as response:
                payload = json.load(response)
            if payload.get("retCode") != 0:
                raise ValueError(f"Bybit retCode={payload.get('retCode')}: {payload.get('retMsg')}")
            return payload, host
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            detail = f"{host}: {type(exc).__name__}: {str(exc)[:160]}"
            failures.append(detail)
            time.sleep(0.2)
    raise RuntimeError("; ".join(failures))

def main():
    ROOT.mkdir(exist_ok=True)
    now_ms = int(time.time() * 1000)
    stamp = dt.datetime.fromtimestamp(now_ms / 1000, dt.timezone.utc).isoformat()
    result = {"collected_at_utc": stamp, "collected_at_ms": now_ms, "exchange": "Bybit", "category": "linear", "symbols": {}, "errors": [], "status": "partial"}
    success = 0
    # Fail fast on network/region blocks; do not spam the same inaccessible API 70 times.
    try:
        fetch("/v5/market/kline", {"category": "linear", "symbol": "BTCUSDT", "interval": "15", "limit": 2})
    except Exception as exc:
        diagnostic = {"checked_at_utc": stamp, "status": "unavailable", "reason": str(exc), "successful_requests": 0, "source": "Bybit public REST", "note": "No market data published; prior snapshot, if any, must be checked for staleness"}
        (ROOT / "status.json").write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("BYBIT CONNECTIVITY FAILED: " + str(exc), flush=True)
        raise SystemExit(2)
    for symbol in SYMBOLS:
        item = {"symbol": symbol, "intervals": {}, "ticker": None, "open_interest": None, "funding_history": None, "instrument": None}
        for field, path, params in [
            ("instrument", "/v5/market/instruments-info", {"category": "linear", "symbol": symbol}),
            ("ticker", "/v5/market/tickers", {"category": "linear", "symbol": symbol}),
            ("open_interest", "/v5/market/open-interest", {"category": "linear", "symbol": symbol, "intervalTime": "5min", "limit": 2}),
            ("funding_history", "/v5/market/funding/history", {"category": "linear", "symbol": symbol, "limit": 2}),
        ]:
            try:
                payload, host = fetch(path, params)
                item[field] = {"source_host": host, "server_time_ms": payload.get("time"), "result": payload["result"]}
                success += 1
            except Exception as exc:
                result["errors"].append({"symbol": symbol, "field": field, "error": str(exc)})
        for interval in INTERVALS:
            try:
                payload, host = fetch("/v5/market/kline", {"category": "linear", "symbol": symbol, "interval": interval, "limit": 200})
                candles = payload["result"]["list"]
                # Bybit returns newest first; exclude current incomplete candle using its start time.
                duration_ms = int(interval) * 60_000
                closed = [c for c in candles if int(c[0]) + duration_ms <= now_ms]
                item["intervals"][interval] = {"source_host": host, "server_time_ms": payload.get("time"), "closed_only": True, "columns": ["start_ms","open","high","low","close","volume","turnover"], "candles": closed}
                success += 1
            except Exception as exc:
                result["errors"].append({"symbol": symbol, "field": f"kline_{interval}", "error": str(exc)})
        result["symbols"][symbol] = item
    result["successful_requests"] = success
    result["expected_requests"] = len(SYMBOLS) * (4 + len(INTERVALS))
    result["status"] = "complete" if success == result["expected_requests"] else ("partial" if success else "unavailable")
    target = ROOT / "latest.json"
    target.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{stamp}: {result['status']}, {success}/{result['expected_requests']} requests, {len(result['errors'])} errors")
    (ROOT / "status.json").write_text(json.dumps({"checked_at_utc": stamp, "status": result["status"], "successful_requests": success, "expected_requests": result["expected_requests"], "error_count": len(result["errors"])}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
if __name__ == "__main__":
    main()
