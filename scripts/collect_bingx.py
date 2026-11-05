#!/usr/bin/env python3
"""Public BingX USDT perpetual research snapshots. No trading, credentials or secrets."""
import concurrent.futures as cf
import datetime as dt
import json
import pathlib
import time
import urllib.parse
import urllib.request

BASE="https://open-api.bingx.com"
SYMBOLS=("BTC-USDT","ETH-USDT","SOL-USDT","XRP-USDT","HYPE-USDT","BNB-USDT","DOGE-USDT","LINK-USDT","AVAX-USDT","ADA-USDT")
INTERVALS=("15m","1h","4h")
def fetch(path,params):
    url=BASE+path+"?"+urllib.parse.urlencode(params)
    last=None
    for attempt in range(3):
        try:
            req=urllib.request.Request(url,headers={"Accept":"application/json","User-Agent":"ResearchMarketCollector/1.0"})
            with urllib.request.urlopen(req,timeout=18) as resp:
                doc=json.load(resp)
            if doc.get("code") != 0 or doc.get("data") is None:
                raise ValueError(f"BingX API code={doc.get('code')}, msg={str(doc.get('msg'))[:120]}")
            return doc["data"]
        except (OSError,ValueError) as e:
            last=e
            if attempt<2: time.sleep(1.5*(attempt+1))
    raise RuntimeError(f"{type(last).__name__}: {str(last)[:160]}")
def job(spec):
    symbol,kind,interval=spec
    if kind=="candles":
        data=fetch("/openApi/swap/v3/quote/klines",{"symbol":symbol,"interval":interval,"limit":200})
        if not isinstance(data,list) or len(data)<3: raise ValueError("Insufficient candle rows")
        return symbol,kind,interval,data
    if kind=="open_interest":
        return symbol,kind,interval,fetch("/openApi/swap/v2/quote/openInterest",{"symbol":symbol})
    if kind=="funding":
        return symbol,kind,interval,fetch("/openApi/swap/v2/quote/fundingRate",{"symbol":symbol,"limit":10})
    raise ValueError(kind)
def main():
    now=dt.datetime.now(dt.timezone.utc).isoformat()
    specs=[(s,"candles",i) for s in SYMBOLS for i in INTERVALS]+[(s,k,None) for s in SYMBOLS for k in ("open_interest","funding")]
    snap={"source":"BingX public perpetual REST","exchange":"BingX","market_type":"USDT perpetual","collected_at_utc":now,"symbols":{},"errors":{}}
    for s in SYMBOLS:
        snap["symbols"][s]={"candles":{},"open_interest":None,"funding":None}
    with cf.ThreadPoolExecutor(max_workers=5) as pool:
        tasks={pool.submit(job,spec):spec for spec in specs}
        for future in cf.as_completed(tasks):
            symbol,kind,interval=tasks[future]
            key=f"{symbol}/{kind}"+(f"/{interval}" if interval else "")
            try:
                _,_,_,data=future.result()
                if kind=="candles":
                    snap["symbols"][symbol]["candles"][interval]=data
                else:
                    snap["symbols"][symbol][kind]=data
            except Exception as exc:
                snap["errors"][key]=f"{type(exc).__name__}: {str(exc)[:180]}"
    completed=len(specs)-len(snap["errors"])
    status={"checked_at_utc":now,"source":snap["source"],"successful_requests":completed,"expected_requests":len(specs),"status":"complete" if completed==len(specs) else "partial" if completed else "unavailable","errors":snap["errors"],"note":"Do not treat missing fields or stale snapshots as valid signals"}
    pathlib.Path("data").mkdir(exist_ok=True)
    pathlib.Path("data/status.json").write_text(json.dumps(status,ensure_ascii=False,indent=2)+"\n")
    if completed==len(specs):
        pathlib.Path("data/latest.json").write_text(json.dumps(snap,ensure_ascii=False,separators=(",",":"))+"\n")
    else:
        # Never leave an old snapshot pretending to be the current successful run.
        pathlib.Path("data/latest.json").unlink(missing_ok=True)
    print(json.dumps(status,ensure_ascii=False),flush=True)
    if completed!=len(specs): raise SystemExit(1)
if __name__=="__main__": main()
