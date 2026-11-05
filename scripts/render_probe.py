#!/usr/bin/env python3
"""Read-only Bybit connectivity probe for Render. No API keys, orders or trading."""
import datetime as dt
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOSTS = ("https://api.bybit.com", "https://api.bytick.com")
PATH = "/v5/market/kline?" + urllib.parse.urlencode({"category":"linear","symbol":"BTCUSDT","interval":"15","limit":"3"})

def probe():
    outcomes = []
    for host in HOSTS:
        try:
            req = urllib.request.Request(host + PATH, headers={"User-Agent":"MarketResearchSnapshot/1.0","Accept":"application/json"})
            with urllib.request.urlopen(req, timeout=9) as response:
                payload = json.load(response)
            rows = payload.get("result", {}).get("list", [])
            ok = payload.get("retCode") == 0 and len(rows) >= 1
            outcomes.append({"host":host,"ok":ok,"retCode":payload.get("retCode"),"server_time_ms":payload.get("time"),"candles_received":len(rows)})
            if ok:
                break
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            outcomes.append({"host":host,"ok":False,"error":f"{type(exc).__name__}: {str(exc)[:140]}"})
    return {"checked_at_utc":dt.datetime.now(dt.timezone.utc).isoformat(),"exchange":"Bybit","market":"BTCUSDT linear perpetual","status":"ok" if any(x["ok"] for x in outcomes) else "unavailable","checks":outcomes}

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/health"):
            data = {"service":"bybit-market-collector","status":"ready","note":"Readiness only; does not mean Bybit is accessible"}
        elif self.path == "/probe":
            data = probe()
        else:
            self.send_error(404)
            return
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200 if data["status"] != "unavailable" else 503)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length",str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    print(f"Starting read-only Bybit probe on port {port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
