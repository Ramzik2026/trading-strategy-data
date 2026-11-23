#!/usr/bin/env python3
"""Archive BingX USDT perpetual 15m OHLCV safely in month-partitioned JSONL.

No trading keys. Does not change existing latest.json or existing collectors.
First execution backfills up to 90 days of available exchange 15m data.
Later executions fill only missing candles, allowing time to accumulate >100d.
Intentionally fails closed on inconsistent/missing/invalid market data.
"""
import datetime as dt
import json
import math
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

Q=15*60*1000
DAY=24*60*60*1000
API='https://open-api.bingx.com/openApi/swap/v3/quote/klines'
SYMBOLS=('BTC-USDT','ETH-USDT','SOL-USDT')
ROOT=Path('data/archive/bingx-perpetual-15m')
STATUS=ROOT/'status.json'

def stamp(t):
    return dt.datetime.fromtimestamp(t/1000,dt.timezone.utc).isoformat()

def fetch(symbol,start,end):
    records={}
    for lo in range(start,end,1000*Q):
        hi=min(lo+1000*Q,end)
        p=urllib.parse.urlencode({'symbol':symbol,'interval':'15m',
            'startTime':lo,'endTime':hi-1,'limit':1000})
        req=urllib.request.Request(API+'?'+p,
                                  headers={'User-Agent':'TradingAI-Public-Research-Archive/1.0'})
        for attempt in range(3):
            time.sleep(1.2)
            try:
                with urllib.request.urlopen(req,timeout=45) as f:response=json.load(f)
                if str(response.get('code'))!='0' or not isinstance(response.get('data'),list):
                    raise ValueError('BingX returned invalid data: '+str(response.get('msg')))
                break
            except (OSError,ValueError) as exc:
                if attempt==2:raise RuntimeError(f'{symbol} {stamp(lo)} API failure: {exc}') from exc
                time.sleep(2**attempt)
        for x in response['data']:
            t=int(x['time'])
            if not lo<=t<hi:continue
            if t%Q:raise ValueError('Misaligned candle '+symbol+' '+str(t))
            p=[float(x[k]) for k in ('open','high','low','close','volume')]
            o,h,l,c,v=p
            if not all(math.isfinite(z) for z in p) or not (0<l<=min(o,c)<=max(o,c)<=h and v>=0):
                raise ValueError('Invalid OHLCV '+symbol+' '+str(t))
            row={'time':t,'open':x['open'],'high':x['high'],'low':x['low'],
                 'close':x['close'],'volume':x['volume']}
            if t in records and records[t]!=row:raise ValueError('Conflicting API candle')
            records[t]=row
    expected=(end-start)//Q
    if len(records)!=expected or any(start+i*Q not in records for i in range(expected)):
        raise RuntimeError(f'GAP {symbol}: expected={expected} got={len(records)} '
                           f'range={stamp(start)} to {stamp(end)}')
    return [records[t] for t in sorted(records)]

def path_for(symbol,t):
    d=dt.datetime.fromtimestamp(t/1000,dt.timezone.utc)
    return ROOT/symbol/f'{d.year}-{d.month:02d}.jsonl'

def previous_last(symbol):
    months=sorted((ROOT/symbol).glob('*.jsonl'))
    if not months:return None
    last=None
    for p in months[-1:]:
        with p.open(encoding='utf8') as f:
            for line in f:
                if line.strip():last=json.loads(line)['time']
    return int(last) if last is not None else None

def persist(symbol,records):
    by_month={}
    for x in records:by_month.setdefault(path_for(symbol,x['time']),[]).append(x)
    added=0
    for path,rows in by_month.items():
        old={}
        if path.exists():
            for line in path.read_text(encoding='utf8').splitlines():
                if not line:continue
                z=json.loads(line)
                old[int(z['time'])]=z
        for x in rows:
            t=x['time']
            if t in old and old[t]!=x:
                raise ValueError('Historical exchange OHLCV revised: '+symbol+' '+stamp(t))
            if t not in old:added+=1
            old[t]=x
        ordered=[old[t] for t in sorted(old)]
        if any(ordered[i]['time']+Q!=ordered[i+1]['time'] for i in range(len(ordered)-1)):
            raise ValueError('Archived month contains internal gaps')
        path.parent.mkdir(parents=True,exist_ok=True)
        temp=path.with_suffix('.tmp')
        temp.write_text(''.join(json.dumps(x,separators=(',',':'))+'\n' for x in ordered),encoding='utf8')
        temp.replace(path)
    return added

def main():
    end=int(time.time()*1000)//Q*Q
    if end%Q:raise AssertionError
    stats={}
    # Never use the still-open candle at timestamp 'end'.
    for symbol in SYMBOLS:
        prev=previous_last(symbol)
        start=(end//DAY)*DAY-90*DAY if prev is None else prev+Q
        if start>end:raise RuntimeError('Archive future timestamps '+symbol)
        if start==end:
            print('UP_TO_DATE',symbol,stamp(end),flush=True)
            stats[symbol]={'new':0,'last_close_utc':stamp(end)}
            continue
        if end-start>100*DAY:raise RuntimeError('Historical gap too old to recover '+symbol)
        rows=fetch(symbol,start,end)
        added=persist(symbol,rows)
        stats[symbol]={'new':added,'last_close_utc':stamp(end),'total_fetched':len(rows),
                       'start':stamp(start)}
        print('ARCHIVED',symbol,stats[symbol],flush=True)
    ROOT.mkdir(parents=True,exist_ok=True)
    STATUS.write_text(json.dumps({
        'source':'BingX public USDT perpetual', 'tf':'15m',
        'last_run_utc':stamp(end),'completed':True,'symbols':stats,
        'order_execution':False},indent=2),encoding='utf8')
    print('ARCHIVE_COMPLETE',stamp(end),flush=True)

if __name__=='__main__': main()
