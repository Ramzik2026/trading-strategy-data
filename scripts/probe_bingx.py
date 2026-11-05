#!/usr/bin/env python3
"""Probe read-only BingX perpetual market endpoints; no account credentials."""
import datetime as dt
import json
import pathlib
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://open-api.bingx.com"
CHECKS = [
    ("klines_15m", "/openApi/swap/v3/quote/klines", {"symbol":"BTC-USDT","interval":"15m","limit":3}),
    ("klines_1h", "/openApi/swap/v3/quote/klines", {"symbol":"BTC-USDT","interval":"1h","limit":3}),
    ("klines_4h", "/openApi/swap/v3/quote/klines", {"symbol":"BTC-USDT","interval":"4h","limit":3}),
    ("open_interest", "/openApi/swap/v2/quote/openInterest", {"symbol":"BTC-USDT"}),
    ("funding", "/openApi/swap/v2/quote/fundingRate", {"symbol":"BTC-USDT","limit":2}),
]
def main():
    result={"exchange":"BingX","checked_at_utc":dt.datetime.now(dt.timezone.utc).isoformat(),"checks":{}}
    for name,path,params in CHECKS:
        try:
            url=BASE+path+"?"+urllib.parse.urlencode(params)
            req=urllib.request.Request(url,headers={"User-Agent":"MarketResearchSnapshot/1.0","Accept":"application/json"})
            with urllib.request.urlopen(req,timeout=12) as resp:
                payload=json.load(resp)
            data=payload.get("data")
            valid=payload.get("code")==0 and data is not None and (not isinstance(data,list) or len(data)>0)
            result["checks"][name]={"ok":valid,"code":payload.get("code"),"msg":str(payload.get("msg",""))[:120],"items":len(data) if isinstance(data,list) else None}
        except (OSError,ValueError,json.JSONDecodeError) as exc:
            result["checks"][name]={"ok":False,"error":f"{type(exc).__name__}: {str(exc)[:160]}"}
    result["status"]="complete" if all(v["ok"] for v in result["checks"].values()) else "partial" if any(v["ok"] for v in result["checks"].values()) else "unavailable"
    pathlib.Path("data").mkdir(exist_ok=True)
    pathlib.Path("data/bingx-probe.json").write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n")
    print(json.dumps(result,ensure_ascii=False),flush=True)
if __name__=="__main__": main()
