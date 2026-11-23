#!/usr/bin/env python3
import datetime as dt, urllib.parse, urllib.request, json, time
from year_backtest import stamp,iso,HOUR
from prb_v14 import Q
api='https://open-api.bingx.com/openApi/swap/v3/quote/klines'
for tf,step in [('15m',Q),('1h',HOUR)]:
 for date in ['2025-10-01','2026-01-01','2026-05-01','2026-08-01','2026-11-01']:
  begin=stamp(date);end=begin+1000*step
  p=urllib.parse.urlencode({'symbol':'BTC-USDT','interval':tf,'startTime':begin,'endTime':end-1,'limit':1000})
  time.sleep(1.15)
  with urllib.request.urlopen(api+'?'+p,timeout=30) as f:obj=json.load(f)
  a=obj.get('data',[]);ts=sorted(int(x['time']) for x in a)
  valid=[x for x in ts if begin<=x<end]
  print('PAGE',tf,date,'code',obj.get('code'),'rows',len(a),'within',len(valid),
        'first',iso(ts[0]) if ts else None,'last',iso(ts[-1]) if ts else None,
        'requested',iso(begin),iso(end),flush=True)
