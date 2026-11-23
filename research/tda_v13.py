#!/usr/bin/env python3
"""TDA research v1.3: 1M/1W context + independent 4H FVG, SNR, 1H RB.

Read-only BingX. No live orders. This is a falsifiable, simplified hypothesis
implementation, NOT a claim of full Pixel/TDA strategy equivalence.
"""
import argparse
import bisect
import collections
import datetime as dt
import json
import math
import time
import urllib.parse
import urllib.request
from pathlib import Path
from year_backtest import Bar, HOUR, FOUR, UTC, API, fetch, stamp, iso, atr, risk, exitbar

WEEK=7*24*HOUR
DAY=24*HOUR

def next_month(t):
    x=dt.datetime.fromtimestamp(t/1000,UTC)
    return int(dt.datetime(x.year+(x.month==12), x.month%12+1,1,tzinfo=UTC).timestamp()*1000)

def bar_close(t,tf):
    if tf=='1M':return next_month(t)
    if tf=='1w':return t+WEEK
    if tf=='1d':return t+DAY
    if tf=='4h':return t+FOUR
    return t+HOUR

def get_htf(symbol,tf,start,end):
    """For monthly/weekly history a single API page is sufficient (limit 1000).
    Fails closed if historical pagination is needed or format invalid."""
    p=urllib.parse.urlencode({'symbol':symbol,'interval':tf,'startTime':start,'endTime':end-1,'limit':1000})
    req=urllib.request.Request(API+'?'+p,headers={'User-Agent':'Trading-AI-TDA-Research/1.3'})
    time.sleep(1.15)
    with urllib.request.urlopen(req,timeout=40) as f:r=json.load(f)
    if str(r.get('code'))!='0' or not isinstance(r.get('data'),list):
        raise RuntimeError('BingX '+tf+' API failed: '+str(r.get('msg')))
    by_t={}
    for item in r['data']:
        t=int(item['time'])
        if not start<=t<end:continue
        row=Bar(t,*(float(item[k]) for k in ['open','high','low','close','volume']))
        if not all(math.isfinite(v) for v in (row.o,row.h,row.l,row.c,row.v)) or row.l>min(row.o,row.c) or row.h<max(row.o,row.c):
            raise ValueError('Bad '+tf+' OHLC '+iso(t))
        if t in by_t and by_t[t]!=row:raise ValueError('Conflicting '+tf+' duplicate')
        by_t[t]=row
    a=[by_t[t] for t in sorted(by_t) if bar_close(t,tf)<=end]
    if len(a)<(25 if tf=='1M' else 100):raise RuntimeError('Insufficient '+tf+' historical warmup: '+str(len(a)))
    if tf=='1M' and any(bar_close(a[i].t,tf)!=a[i+1].t for i in range(len(a)-1)):raise RuntimeError('MONTHLY_DATA_GAPS')
    if tf=='1w' and any(a[i+1].t-a[i].t!=WEEK for i in range(len(a)-1)):raise RuntimeError('WEEKLY_DATA_GAPS')
    return a

def pivot_flags(a,side,left=2,right=2):
    """A pivot is known only after all right-hand candles have closed."""
    found=[];flags=[]
    for i in range(len(a)):
        j=i-right
        if j>=left:
            x=getattr(a[j],side)
            vals=[getattr(c,side) for c in a[j-left:i+1]]
            if vals.count(x)==1 and (x==max(vals) if side=='h' else x==min(vals)):
                found.append((i,j,x))
        flags.append(tuple(found[-2:]))
    return flags

def structural_states(a):
    hi=pivot_flags(a,'h');lo=pivot_flags(a,'l');out=[]
    for i in range(len(a)):
        down=len(hi[i])==2 and hi[i][-1][2]<hi[i][-2][2]
        up=len(lo[i])==2 and lo[i][-1][2]>lo[i][-2][2]
        out.append(-1 if down and not up else 1 if up and not down else 0)
    return out

def overlap(a,b):
    return max(a['lo'],b['lo'])<=min(a['hi'],b['hi'])

def closed_zone_events(bars,tf):
    """FVG: 3 candles; precisely the 4th must wick-test the zone, body crossing
    the whole zone invalidates. SNR: opposing pair forms level, later close
    crosses level, later retest confirms; break may be non-adjacent."""
    rows=[];pending=[];snr=[]
    stats=collections.Counter()
    for i,b in enumerate(bars):
        # 1. Existing, previously confirmed SNR zones; body invalidation first
        alive=[]
        for z in snr:
            if z['status']=='WAIT_BREAK':
                if z['side']==1 and i>z['born'] and i>0 and bars[i-1].c<=z['level']<b.c:
                    z['status']='WAIT_RETEST';z['broken']=i;stats['snr_break']+=1
                elif z['side']==-1 and i>z['born'] and i>0 and bars[i-1].c>=z['level']>b.c:
                    z['status']='WAIT_RETEST';z['broken']=i;stats['snr_break']+=1
            elif z['status']=='WAIT_RETEST' and i>z['broken']:
                if b.l<=z['hi'] and b.h>=z['lo']:
                    through=b.o>=z['hi'] and b.c<=z['lo'] if z['side']==1 else b.o<=z['lo'] and b.c>=z['hi']
                    holding=b.c>=z['level'] if z['side']==1 else b.c<=z['level']
                    if holding and not through:
                        rows.append(dict(z,confirmed_at=bar_close(b.t,tf)))
                        stats['snr_tested']+=1
                    else:stats['snr_invalidated']+=1
                    continue
            if i-z['born']<=50:alive.append(z)
        snr=alive
        # 2. 4th-candle FVG test
        keep=[]
        for z in pending:
            if i==z['born']+1:
                touch=b.l<=z['hi'] and b.h>=z['lo']
                through=(b.o>=z['hi'] and b.c<=z['lo']) if z['side']==1 else (b.o<=z['lo'] and b.c>=z['hi'])
                holds=(b.c>z['lo']) if z['side']==1 else (b.c<z['hi'])
                if touch and not through and holds:
                    rows.append(dict(z,confirmed_at=bar_close(b.t,tf)))
                    stats['fvg_tested']+=1
                else:stats['fvg_rejected_4th']+=1
            elif i<=z['born']:keep.append(z)
        pending=keep
        # 3. New three-candle gap is only known at third candle close
        if i>=2:
            for side,lo,hi in [(1,bars[i-2].h,b.l),(-1,b.h,bars[i-2].l)]:
                if lo<hi and (hi-lo)/lo>=0.001:
                    pending.append(dict(kind='FVG',side=side,lo=lo,hi=hi,born=i,level=None))
                    stats['fvg_formed']+=1
        # 4. Opposite-colored pair; level close1 = open2 within feed tolerance.
        if i>=1:
            p=bars[i-1]
            opposite=(p.c<p.o and b.c>b.o) or (p.c>p.o and b.c<b.o)
            level=p.c
            if opposite and abs(level-b.o)<=max(level*0.00002,0.01):
                side=1 if p.c<p.o else -1
                lo=min(p.l,b.l) if side==1 else level
                hi=level if side==1 else max(p.h,b.h)
                if hi>lo:
                    snr.append(dict(kind='SNR',side=side,lo=lo,hi=hi,level=level,
                                    born=i,status='WAIT_BREAK',broken=None))
                    stats['snr_pairs']+=1
    # Merge overlapping same-direction zones with no temporal lookahead: merge only
    # already-confirmed historical zones, and announce the combined interval at
    # the later of the two confirmation timestamps. V1.3 reports raw events too.
    return rows,dict(stats)

def bias_at(monthly,weekly,month_events,week_events,hour_close):
    """No lower timeframe trend. Strict directional agreement of monthly and
    weekly confirmed fractal structure and already-tested directional zones."""
    def at(a,tf):
        times=[bar_close(b.t,tf) for b in a]
        return bisect.bisect_right(times,hour_close)-1
    mi=at(monthly,'1M');wi=at(weekly,'1w')
    if mi<0 or wi<0:return (0,'NOT_CLOSED')
    ms=structural_states(monthly)[mi]
    ws=structural_states(weekly)[wi]
    if not ms or not ws or ms!=ws:return (0,'FRACTAL_CONFLICT')
    def active(events,tf,side,now,max_candles):
        arr=[z for z in events if z['side']==side and z['confirmed_at']<=now]
        if not arr:return False
        last=max(z['confirmed_at'] for z in arr)
        return sum(1 for b in (monthly if tf=='1M' else weekly) if last<=bar_close(b.t,tf)<=now)<=max_candles
    if not active(month_events,'1M',ms,hour_close,12):
        return (0,'MONTH_NO_TESTED_ZONE')
    if not active(week_events,'1w',ws,hour_close,12):
        return (0,'WEEK_NO_TESTED_ZONE')
    return (ms,'ALIGNED')

def hourly_signals(hour, four, month, week):
    """4H FVG/SNR event -> next available CLOSED 1H confirmation.
    RB: placeholder 1H two-bar rejection, guarded by 4H confirmed fractal.
    Full 15m RB confirmation is NOT yet implemented; RB signals are diagnostics.
    """
    fzones,fs=closed_zone_events(four,'4h')
    mzones,ms=closed_zone_events(month,'1M')
    wzones,ws=closed_zone_events(week,'1w')
    fcloses=[b.t+FOUR for b in four]
    hp=pivot_flags(four,'h');lp=pivot_flags(four,'l')
    events={'FVG':{},'SNR':{},'RB_DIAGNOSTIC_ONLY':{}}
    all_context=collections.Counter()
    bias_series=[]
    for i,b in enumerate(hour):
        now=b.t+HOUR
        # bias calculated exclusively with closed 1M and 1W bars
        direction,reason=bias_at(month,week,mzones,wzones,now)
        bias_series.append(direction)
        all_context[reason]+=1
        if i<10:continue
        j=bisect.bisect_right(fcloses,now)-1
        if j<10:continue
        # Confirm by close across previous hour high or low (no same-bar zone)
        for kind in ('FVG','SNR'):
            candidates=[z for z in fzones if z['kind']==kind and z['side']==direction and
                        0<now-z['confirmed_at']<=24*HOUR]
            if not candidates:continue
            z=max(candidates,key=lambda x:x['confirmed_at'])
            trigger=b.c>hour[i-1].h if direction==1 else b.c<hour[i-1].l
            if trigger:
                a=atr(hour[:i+1],14)[-1] or 0
                stop=z['lo']-.15*a if direction==1 else z['hi']+.15*a
                if stop>0 and (b.c-stop)*direction>0:
                    target=b.c+direction*2.5*abs(b.c-stop)
                    if target>0:
                        events[kind][i]=(direction,stop,target)
        # 1H RB candidate after closed 4H pivot sweep and two opposite candles.
        # Detect only; confirm on closed 15m later before enabling trading.
        prev=hour[i-1]
        if direction and i>=3:
            lows=lp[j-1];highs=hp[j-1]
            if direction==1 and len(lows)>0:
                level=lows[-1][2]
                if prev.c<prev.o and prev.l<level and b.c>b.o and b.c>level:
                    st=min(prev.l,b.l)
                    events['RB_DIAGNOSTIC_ONLY'][i]=(1,st,b.c+2.5*(b.c-st))
            if direction==-1 and len(highs)>0:
                level=highs[-1][2]
                if prev.c>prev.o and prev.h>level and b.c<b.o and b.c<level:
                    st=max(prev.h,b.h)
                    events['RB_DIAGNOSTIC_ONLY'][i]=(-1,st,b.c-2.5*(st-b.c))
    return events,dict(all_context),{'4h':fs,'1M':ms,'1w':ws,'zone_events':{
        '4h':len(fzones),'1M':len(mzones),'1w':len(wzones)}},bias_series

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--symbol',default='BTC-USDT')
    p.add_argument('--start',default='2025-11-23')
    p.add_argument('--end',default='2026-11-23')
    p.add_argument('--output',default='research/results/tda_v13.json')
    a=p.parse_args()
    if a.symbol not in ['BTC-USDT','ETH-USDT','SOL-USDT']:p.error('Unsupported symbol')
    start,end=stamp(a.start),stamp(a.end)
    if not start<end<=int(time.time()*1000)//HOUR*HOUR:p.error('Invalid date range')
    h=fetch(a.symbol,'1h',start-30*DAY,end,HOUR)
    f=fetch(a.symbol,'4h',start-30*DAY,end,FOUR)
    # Separate older 1M/1W history so pivots are not initialized from only one year.
    months=get_htf(a.symbol,'1M',stamp('2022-01-01'),end)
    weeks=get_htf(a.symbol,'1w',stamp('2022-01-01'),end)
    events,reasons,stats,bias=hourly_signals(h,f,months,weeks)
    out={'version':'v1.3-exploratory','symbol':a.symbol,'window':[a.start,a.end],
         'execution':'DISABLED','notes':[
             'Research prototype; RB_DIAGNOSTIC_ONLY is never traded (requires 15m confirmation)',
             'Strict 1M AND 1W fractal and tested FVG/SNR gates, no 4H trend detection',
             'FVG is valid only with fourth-candle touch; SNR breakout may be delayed',
             'Entry model not claimed to replicate complete TDA fractal targets, three partial fills, or RB rules',
             'No inference of profitability from signal counts'],
         'counts':{'1h':len(h),'4h':len(f),'1M':len(months),'1w':len(weeks)},
         'bias_reasons':reasons,'zone_stats':stats,
         'candidate_counts':{name:sum(start<=h[i].t+HOUR<end for i in series) for name,series in events.items()},
         'candidates':{name:[{'signal_closed_at':iso(h[i].t+HOUR),'side':'LONG' if e[0]==1 else 'SHORT',
                               'stop':e[1],'target':e[2]} for i,e in series.items()
                               if start<=h[i].t+HOUR<end][:150] for name,series in events.items()}}
    target=Path(a.output);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
    print('DATA',out['counts'])
    print('ZONES',json.dumps(stats,sort_keys=True))
    print('BIAS',reasons)
    print('CANDIDATES',out['candidate_counts'])
    print('REPORT',target)

if __name__=='__main__':main()
