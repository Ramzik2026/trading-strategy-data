#!/usr/bin/env python3
"""Read-only BingX 4H FVG with independent 1W trend and 1H BOS hypothesis."""
import argparse, json, math, statistics, time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

HOUR_MS=3600000
FOUR_MS=4*HOUR_MS
WEEK_MS=7*24*HOUR_MS
WAIT_HOURS=48
BOS_WAIT_HOURS=12
HOLD_HOURS=48
MIN_GAP_PCT=.1
STOP_BUFFER=.0005
RR=2.
HYPOTHETICAL_COST_BPS=14
BASE_URL='https://open-api.bingx.com/openApi/swap/v3/quote/klines'

@dataclass(frozen=True)
class Candle:
    t:int
    o:float
    h:float
    l:float
    c:float

@dataclass(frozen=True)
class Gap:
    i:int
    side:int
    low:float
    high:float
    width_pct:float

def parse_rows(rows,start,end,step):
    by_t={}
    for obj in rows:
        if not isinstance(obj,dict):raise ValueError('BingX returned non-object candle')
        t=int(obj['time'])
        if not start<=t<end or t%step:continue
        bar=Candle(t,*(float(obj[k]) for k in ('open','high','low','close')))
        if not all(math.isfinite(z) and z>0 for z in (bar.o,bar.h,bar.l,bar.c)):raise ValueError('Invalid price')
        if not (bar.l<=min(bar.o,bar.c)<=max(bar.o,bar.c)<=bar.h):raise ValueError('Invalid OHLC bounds')
        if t in by_t and by_t[t]!=bar:raise ValueError('Conflicting duplicates')
        by_t[t]=bar
    out=[by_t[t] for t in sorted(by_t)]
    if len(out)!=(end-start)//step or any(x.t!=start+i*step for i,x in enumerate(out)):
        raise ValueError('Missing candles: no interpolation permitted')
    return out

def remote_page(symbol,interval,start,end):
    url=BASE_URL+'?'+urlencode({'symbol':symbol,'interval':interval,'startTime':start,'endTime':end-1,'limit':400})
    error=None
    for attempt in range(4):
        try:
            request=Request(url,headers={'User-Agent':'BingX-FVG-Structure-Research/4.0','Accept':'application/json'})
            with urlopen(request,timeout=25) as resp:result=json.load(resp)
            if not isinstance(result,dict) or str(result.get('code',0))!='0' or not isinstance(result.get('data'),list):
                raise ValueError('Unexpected public API payload')
            return result['data']
        except (HTTPError,URLError,TimeoutError,ValueError) as e:
            error=f'{type(e).__name__}: {e}'[:160]
            if attempt<3:time.sleep(2**attempt+1)
    raise RuntimeError(f'BingX {symbol} {interval} failed: {error}')

def download(symbol,interval,start,end):
    if not symbol.endswith('-USDT') or not symbol.replace('-','').isalnum():raise ValueError('Unexpected contract')
    step={'1h':HOUR_MS,'4h':FOUR_MS}[interval]
    total=(end-start)//step
    raw=[]
    for offset in range(0,total,390):
        left=start+offset*step
        right=min(left+390*step,end)
        raw.extend(remote_page(symbol,interval,left,right))
        time.sleep(1.08)
    return parse_rows(raw,start,end,step)

def verify_consistency(h1,h4):
    index={x.t:x for x in h1}
    for b in h4:
        pack=[index.get(b.t+i*HOUR_MS) for i in range(4)]
        if any(x is None for x in pack):raise ValueError('Missing 1H data inside 4H bar')
        estimated=(pack[0].o,max(x.h for x in pack),min(x.l for x in pack),pack[-1].c)
        actual=(b.o,b.h,b.l,b.c)
        if any(abs(a-z)>max(1e-6,abs(z)*1e-5) for a,z in zip(actual,estimated)):
            raise ValueError(f'1H/4H data mismatch at {b.t}: {actual} != {estimated}')

def gaps_from_4h(c):
    out=[]
    for i in range(2,len(c)):
        a,b=c[i-2],c[i]
        side=1 if b.l>a.h else -1 if b.h<a.l else 0
        if not side:continue
        low,high=(a.h,b.l) if side==1 else (b.h,a.l)
        width=100*(high-low)/low
        if width>MIN_GAP_PCT:out.append(Gap(i,side,low,high,width))
    return out

def weekly_filter(h4):
    per_week={}
    for b in h4:
        d=datetime.fromtimestamp(b.t/1000,timezone.utc)
        mon=(d-timedelta(days=d.weekday(),hours=d.hour,minutes=d.minute)).replace(second=0,microsecond=0)
        key=int(mon.timestamp()*1000)
        per_week.setdefault(key,[]).append(b)
    full=[]
    for key,items in sorted(per_week.items()):
        if len(items)==42 and all(x.t==key+j*FOUR_MS for j,x in enumerate(items)):
            full.append((key+WEEK_MS,items[-1].c))
    out={};pointer=0
    for bar in h4:
        asof=bar.t+FOUR_MS
        while pointer<len(full) and full[pointer][0]<=asof:pointer+=1
        trend=0
        if pointer>=8:
            vals=[v for _,v in full[pointer-8:pointer]]
            sma=sum(vals)/8
            if vals[-1]>sma and vals[-1]>vals[-2]:trend=1
            elif vals[-1]<sma and vals[-1]<vals[-2]:trend=-1
        out[bar.t]=trend
    return out

def known_pivots(h1):
    highs=[None]*len(h1);lows=[None]*len(h1)
    last_high=None;last_low=None
    for k in range(len(h1)):
        p=k-2
        if p>=2:
            seq=h1[p-2:p+3]
            if all(seq[2].h>x.h for j,x in enumerate(seq) if j!=2):last_high=(p,seq[2].h)
            if all(seq[2].l<x.l for j,x in enumerate(seq) if j!=2):last_low=(p,seq[2].l)
        highs[k]=last_high;lows[k]=last_low
    return highs,lows

def hit_zone(b,g):return b.l<=g.high and b.h>=g.low
def stop_price(g):return g.low*(1-STOP_BUFFER) if g.side==1 else g.high*(1+STOP_BUFFER)
def stop_touched(b,g):
    stop=stop_price(g)
    return b.l<=stop if g.side==1 else b.h>=stop

def bos_at(c,k,g,pivots):
    if k<=0:return False
    p=pivots[0][k] if g.side==1 else pivots[1][k]
    if not p or k-p[0]>48:return False
    level=p[1]
    return (c[k-1].c<=level<c[k].c) if g.side==1 else (c[k-1].c>=level>c[k].c)

def run_trade(h1,k_entry,g,*,cost_bps=HYPOTHETICAL_COST_BPS):
    if k_entry+HOLD_HOURS>len(h1):return None
    entry=h1[k_entry].o
    sl=stop_price(g)
    risk=(entry-sl)*g.side
    if risk<=0 or not math.isfinite(risk):return None
    tp=entry+g.side*RR*risk
    gross=0.;reason='time';duration=HOLD_HOURS
    for j,b in enumerate(h1[k_entry:k_entry+HOLD_HOURS]):
        stop_hit=b.l<=sl if g.side==1 else b.h>=sl
        tp_hit=b.h>=tp if g.side==1 else b.l<=tp
        if stop_hit:gross=-1.;reason='stop';duration=j+1;break
        if tp_hit:gross=RR;reason='target';duration=j+1;break
    else:gross=g.side*(h1[k_entry+HOLD_HOURS-1].c-entry)/risk
    fee=(cost_bps/10000)*entry/risk
    return {'entry':entry,'stop':sl,'target':tp,'risk':risk,
            'entry_utc':datetime.fromtimestamp(h1[k_entry].t/1000,timezone.utc).isoformat(),
            'reason':reason,'bars_held':duration,'gross_r':gross,'cost_r':fee,'net_r':gross-fee}

def forced_stop_at_touch(g,h1,k_touch,*,cost_bps=HYPOTHETICAL_COST_BPS):
    # Near-edge limit filled AND stop crossed within first-touching hour.
    # Such a losing trade must NOT be removed by using next-hour open.
    entry=g.high if g.side==1 else g.low
    sl=stop_price(g)
    risk=(entry-sl)*g.side
    fee=cost_bps/10000*entry/risk
    return {'entry':entry,'stop':sl,'target':entry+g.side*RR*risk,'risk':risk,
            'entry_utc':datetime.fromtimestamp(h1[k_touch].t/1000,timezone.utc).isoformat(),
            'reason':'stop','bars_held':1,'gross_r':-1.,'cost_r':fee,'net_r':-1.-fee}

def find_candidate(h1,h4,g,pivots):
    born=h4[g.i].t+FOUR_MS
    start=(born-h1[0].t)//HOUR_MS
    if not 0<=start<len(h1):return None,None,None
    touch=None
    for j in range(start,min(start+WAIT_HOURS,len(h1))):
        if hit_zone(h1[j],g):touch=j;break
    if touch is None:return None,None,None
    if stop_touched(h1[touch],g):return None,None,touch
    base=touch+1
    confirmed=None
    for k in range(touch,min(touch+BOS_WAIT_HOURS,len(h1))):
        if stop_touched(h1[k],g):break
        if bos_at(h1,k,g,pivots):
            confirmed=k+1
            break
    return base,confirmed,None

def summarize(rows):
    if not rows:return {'n':0,'avg_net_r':None,'avg_gross_r':None,'total_net_r':0.,'wins':0,'tp':0,'stop':0,'time':0}
    net=[x['net_r'] for x in rows]
    return {'n':len(rows),'avg_net_r':round(statistics.mean(net),5),
            'avg_gross_r':round(statistics.mean(x['gross_r'] for x in rows),5),
            'total_net_r':round(sum(net),5),
            'wins':sum(x>0 for x in net),'tp':sum(x['reason']=='target' for x in rows),
            'stop':sum(x['reason']=='stop' for x in rows),'time':sum(x['reason']=='time' for x in rows)}

def analyze(h4,h1,*,cost_bps=HYPOTHETICAL_COST_BPS):
    verify_consistency(h1,h4)
    weeks=weekly_filter(h4)
    pivots=known_pivots(h1)
    split=int(len(h4)*.60)
    signals=[]
    detected=gaps_from_4h(h4)
    for g in detected:
        if g.i<420 or g.i+28>=len(h4):continue
        baseline_entry,bos_entry,instant_stop=find_candidate(h1,h4,g,pivots)
        if baseline_entry is None and instant_stop is None:continue
        weekly=weeks[h4[g.i].t]
        row={'formed_utc':datetime.fromtimestamp((h4[g.i].t+FOUR_MS)/1000,timezone.utc).isoformat(),
             'signal_index':g.i,'period':'holdout' if g.i>=split else 'development',
             'side':'long' if g.side==1 else 'short','width_pct':round(g.width_pct,5),
             'weekly_trend':weekly,'bos_found':bos_entry is not None,'outcomes':{}}
        configs={'baseline':baseline_entry,'weekly':baseline_entry if weekly==g.side else None,
                 'bos':bos_entry,'weekly_bos':bos_entry if weekly==g.side else None}
        for key,entry in configs.items():
            instant=instant_stop is not None and (key=='baseline' or (key=='weekly' and weekly==g.side))
            ans=(forced_stop_at_touch(g,h1,instant_stop,cost_bps=cost_bps) if instant
                 else run_trade(h1,entry,g,cost_bps=cost_bps) if entry is not None else None)
            if ans is not None:row['outcomes'][key]=ans
        signals.append(row)
    periods={}
    for period in ('development','holdout','all'):
        rows=[s for s in signals if period=='all' or s['period']==period]
        periods[period]={v:summarize([s['outcomes'][v] for s in rows if v in s['outcomes']])
                         for v in ('baseline','weekly','bos','weekly_bos')}
        periods[period]['signals_with_retest']=len(rows)
        periods[period]['weekly_alignment']=sum(x['weekly_trend']==(1 if x['side']=='long' else -1) for x in rows)
        periods[period]['bos_detected']=sum(x['bos_found'] for x in rows)
    return {'study':periods,'signals':signals,
            'counts':{'4h_bars':len(h4),'1h_bars':len(h1),'gaps_all':len(detected),'first_retest_candidates':len(signals)},
            'parameters':{'fvg_min_width_percent_strictly_greater':.1,
                          'weekly_trend':'last CLOSED weekly close vs SMA8 AND previous weekly close',
                          'BOS':'1H close crosses a CONFIRMED two-left two-right pivot aged <=48h',
                          'BOS_wait_hours_after_touch':BOS_WAIT_HOURS,'touch_wait_hours':WAIT_HOURS,
                          'max_holding_hours':HOLD_HOURS,'reward_risk':RR,
                          'stop_buffer_percent':STOP_BUFFER*100,
                          'hypothetical_cost_bps':cost_bps,'holdout_last_fraction':.40,'no_orders':True},
            'limitations':['Selected trades may be too few to support an edge.',
                           'Stop-first 1H bars, not tick data.',
                           'Signals may overlap: summed net-R is not portfolio equity.',
                           'Funding, liquidation, actual fees/spread/slippage not tested.',
                           'Holdout parameters must not be tuned using final results.']}

def markdown_report(results):
    lines=['# Independent annual FVG + 1W trend + 1H BOS study','',
           'Hypothetical per-signal R-multiples, NOT account returns. 14 bps assumed costs.',
           'Identical 4H FVG candidates, weekly based only on completed weeks, BOS on confirmed 1H fractals.',
           '', '| Pair | Holdout base n / avg R | Weekly n / avg R | BOS n / avg R | Both n / avg R |',
           '|---|---:|---:|---:|---:|']
    for sym,d in results.items():
        group=d['result']['study']['holdout']
        def cell(v):
            x=group[v]
            return f"{x['n']} / {x['avg_net_r']:+.3f}R" if x['n'] else '0 / —'
        lines.append(f"| {sym} | {cell('baseline')} | {cell('weekly')} | {cell('bos')} | {cell('weekly_bos')} |")
    lines+=['','### Limitations','',
            'No real orders, funding, actual fees, liquidation or tick event ordering.',
            'Trades may overlap; averages do not measure realized portfolio returns.']
    return '\n'.join(lines)+'\n'

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--symbols',nargs='+',default=['BTC-USDT','ETH-USDT','SOL-USDT'])
    p.add_argument('--days',type=int,default=365)
    p.add_argument('--out',type=Path,default=Path('research-structure-output'))
    p.add_argument('--cost-bps',type=float,default=HYPOTHETICAL_COST_BPS)
    args=p.parse_args()
    if not 90<=args.days<=730 or not 0<=args.cost_bps<=100:p.error('days 90..730; cost 0..100')
    args.out.mkdir(parents=True,exist_ok=True)
    end=int(time.time()*1000)//FOUR_MS*FOUR_MS
    start=end-args.days*6*FOUR_MS
    results={}
    for sym in args.symbols:
        print('Fetching',sym,'4H',flush=True)
        h4=download(sym,'4h',start,end)
        print('Fetching',sym,'1H',flush=True)
        h1=download(sym,'1h',start,end)
        result=analyze(h4,h1,cost_bps=args.cost_bps)
        results[sym]={'asof':datetime.fromtimestamp(end/1000,timezone.utc).isoformat(),'result':result}
        (args.out/f'{sym}_result.json').write_text(json.dumps(results[sym],indent=2)+'\n',encoding='utf-8')
        (args.out/f'{sym}_4h.json').write_text(json.dumps([b.__dict__ for b in h4]),encoding='utf-8')
        (args.out/f'{sym}_1h.json').write_text(json.dumps([b.__dict__ for b in h1]),encoding='utf-8')
        print(sym,'holdout:',json.dumps(result['study']['holdout']),flush=True)
    (args.out/'summary.json').write_text(json.dumps(results,indent=2)+'\n',encoding='utf-8')
    report=markdown_report(results)
    (args.out/'REPORT.md').write_text(report,encoding='utf-8')
    print(report,flush=True)

if __name__=='__main__':main()
