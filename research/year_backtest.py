#!/usr/bin/env python3
"""Read-only BingX perpetual backtest. No keys and no order endpoints.

V1.2 reproducibility experiment. This intentionally mirrors the v1.1 agent
conditions in one deployable file; it is not the complete Pixel/TDA strategy.
"""
import argparse
import bisect
import datetime as dt
import hashlib
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

UTC = dt.timezone.utc
HOUR = 3600000
FOUR = 4 * HOUR
API = 'https://open-api.bingx.com/openApi/swap/v3/quote/klines'
NAMES = ['SMC_FVG_BOS','PriceAction','Trend','Supervisor','Supervisor_Context']

@dataclass(frozen=True)
class Bar:
    t: int
    o: float
    h: float
    l: float
    c: float
    v: float


def stamp(s):
    return int(dt.datetime.strptime(s,'%Y-%m-%d').replace(tzinfo=UTC).timestamp()*1000)


def iso(t):
    return dt.datetime.fromtimestamp(t/1000, UTC).isoformat()


def fetch(symbol, tf, start, end, step):
    """Fail closed if any OHLC bar missing; obey 1 request per second per IP."""
    bars = {}
    for lo in range(start,end,1000*step):
        hi = min(lo+1000*step,end)
        p = urllib.parse.urlencode({'symbol':symbol,'interval':tf,'startTime':lo,
                                    'endTime':hi-1,'limit':1000})
        req = urllib.request.Request(API+'?'+p,headers={'User-Agent':'Trading-AI-Audit/1.2'})
        for attempt in range(4):
            time.sleep(1.15)
            try:
                with urllib.request.urlopen(req,timeout=35) as f: obj=json.load(f)
                if str(obj.get('code'))!='0' or not isinstance(obj.get('data'),list):
                    raise ValueError('Invalid BingX API result '+str(obj.get('code'))+' '+str(obj.get('msg')))
                break
            except (OSError, ValueError) as e:
                if attempt==3: raise RuntimeError('Failed query '+tf+' '+iso(lo)+': '+str(e)) from e
                time.sleep(2**attempt)
        for b in obj['data']:
            t=int(b['time'])
            if not lo<=t<hi: continue
            o,h,l,c,v=(float(b[x]) for x in ('open','high','low','close','volume'))
            if not all(math.isfinite(x) for x in (o,h,l,c,v)) or not (0<l<=min(o,c)<=max(o,c)<=h) or v<0:
                raise ValueError('Malformed '+tf+' bar at '+str(t))
            row=Bar(t,o,h,l,c,v)
            if t in bars and bars[t]!=row: raise ValueError('Conflicting duplicate candle')
            bars[t]=row
    result=[bars[k] for k in sorted(bars)]
    if len(result)!=(end-start)//step or any(b.t!=start+i*step for i,b in enumerate(result)):
        raise ValueError('INCOMPLETE '+tf+' history: got '+str(len(result))+' expected '+str((end-start)//step))
    return result


def ema(a,n):
    result=[None]*len(a)
    if len(a)<n:return result
    v=sum(a[:n])/n
    result[n-1]=v
    for i in range(n,len(a)):
        v=(2*a[i]+(n-1)*v)/(n+1)
        result[i]=v
    return result


def atr(bars,n):
    tr=[];res=[None]*len(bars)
    for i,b in enumerate(bars):
        tr.append(max(b.h-b.l,abs(b.h-bars[i-1].c),abs(b.l-bars[i-1].c)) if i else b.h-b.l)
        if i>=n-1:res[i]=sum(tr[i-n+1:i+1])/n
    return res


def piv(b,i,k):
    p=i-3
    if p<3:return False
    val=getattr(b[p],k)
    s=[getattr(x,k) for x in b[p-3:i+1]]
    return s.count(val)==1 and (val==max(s) if k=='h' else val==min(s))


def signals(one,four):
    av=atr(one,14)
    smc={}; pa={}; tr={}; bias=[]
    last_h=last_l=None
    bos_up=bos_dn=-1000000
    zones=[]
    e1=ema([b.c for b in one],20)
    e20=ema([b.c for b in four],20)
    e50=ema([b.c for b in four],50)
    closes=[b.t+FOUR for b in four]
    aligned=[bisect.bisect_right(closes,b.t+HOUR)-1 for b in one]
    for i,bar in enumerate(one):
        j=aligned[i]
        direction='FLAT'
        if j>=49:
            if four[j].c>e20[j]>e50[j]:direction='LONG'
            elif four[j].c<e20[j]<e50[j]:direction='SHORT'
        bias.append(direction)
        if i>=35 and j>=54:
            if direction=='LONG' and one[i-1].c<=e1[i-1] and bar.c>e1[i] and bar.c>bar.o:
                st=min(bar.c-1.5*av[i],min(x.l for x in one[i-10:i]))
                if 0<st<bar.c:tr[i]=(1,st,bar.c+3*(bar.c-st))
            if direction=='SHORT' and one[i-1].c>=e1[i-1] and bar.c<e1[i] and bar.c<bar.o:
                st=max(bar.c+1.5*av[i],max(x.h for x in one[i-10:i]))
                if st>bar.c:tr[i]=(-1,st,bar.c-3*(st-bar.c))
        if i>=31:
            hi=max(x.h for x in one[i-20:i]); lo=min(x.l for x in one[i-20:i])
            prev_hi=max(x.h for x in one[i-21:i-1]); prev_lo=min(x.l for x in one[i-21:i-1])
            if bar.c>hi and one[i-1].c<=prev_hi:
                st=min(x.l for x in one[i-10:i])
                if st<bar.c:pa[i]=(1,st,bar.c+3*(bar.c-st))
            elif bar.c<lo and one[i-1].c>=prev_lo:
                st=max(x.h for x in one[i-10:i])
                if st>bar.c:pa[i]=(-1,st,bar.c-3*(st-bar.c))
        if i<8:continue
        if piv(one,i,'h'):last_h=one[i-3].h
        if piv(one,i,'l'):last_l=one[i-3].l
        if last_h is not None and one[i-1].c<=last_h<bar.c:bos_up=i
        if last_l is not None and one[i-1].c>=last_l>bar.c:bos_dn=i
        keep=[]
        for z in zones:
            side,lower,upper,born=z
            if i-born>40:continue
            if not (bar.l<=upper and bar.h>=lower):
                keep.append(z);continue
            if i<34:continue
            if side==1 and bar.l>=lower and bar.c>upper:
                st=lower-0.2*av[i]
                if 0<st<bar.c:smc[i]=(1,st,bar.c+2.5*(bar.c-st))
            if side==-1 and bar.h<=upper and bar.c<lower:
                st=upper+0.2*av[i]
                if st>bar.c:smc[i]=(-1,st,bar.c-2.5*(st-bar.c))
        zones=keep
        if bar.l>one[i-2].h:
            lower,upper=one[i-2].h,bar.l
            if 0<=i-bos_up<=6 and (upper-lower)/lower*100>0.1:zones.append((1,lower,upper,i))
        if bar.h<one[i-2].l:
            lower,upper=bar.h,one[i-2].l
            if 0<=i-bos_dn<=6 and (upper-lower)/upper*100>0.1:zones.append((-1,lower,upper,i))
    return {'SMC_FVG_BOS':smc,'PriceAction':pa,'Trend':tr},bias,aligned


def risk(e,op,equity,fee,slip):
    side,stop,target=e
    entry=op*(1+side*slip)
    stop_fill=stop*(1-side*slip)
    target_fill=target*(1-side*slip)
    risk_per_unit=(entry-stop_fill)*side+fee*(entry+stop_fill)
    gain_per_unit=(target_fill-entry)*side-fee*(entry+target_fill)
    if not (stop>0 and target>0 and risk_per_unit>0 and gain_per_unit/risk_per_unit>=2):return None
    qty=min(equity*.005/risk_per_unit,equity*3/entry)
    return (entry,qty) if qty>0 else None


def exitbar(e,bar,opened,slip):
    side,stop,target=e
    if side==1:
        if bar.o<=stop:return bar.o*(1-slip),'STOP_GAP'
        if bar.o>=target:return target*(1-slip),'TARGET_GAP_CAPPED'
        stophit=bar.l<=stop; targethit=bar.h>=target
    else:
        if bar.o>=stop:return bar.o*(1+slip),'STOP_GAP'
        if bar.o<=target:return target*(1+slip),'TARGET_GAP_CAPPED'
        stophit=bar.h>=stop;targethit=bar.l<=target
    if stophit:return stop*(1-side*slip),'STOP_AMBIGUOUS' if targethit else 'STOP'
    if targethit:return target*(1-side*slip),'TARGET'
    if bar.t-opened>=72*HOUR:return bar.c*(1-side*slip),'TIMEOUT'
    return None


def analyze(one,four,start,end,fee=.0005,slippage_bps=2):
    methods,bias,aligned=signals(one,four)
    # Two different source agents needed for legacy Supervisor. Context accepts one,
    # provided its direction matches CLOSED 4H trend and no active agent disagrees.
    def choose(name,i):
        if name in methods:return methods[name].get(i)
        current=[x[i] for x in methods.values() if i in x]
        if name=='Supervisor':
            if len(current)<2 or len({x[0] for x in current})!=1:return None
            side=current[0][0]
            if bias[i] == ('SHORT' if side==1 else 'LONG'):return None
            valid=current
        else:
            side=1 if bias[i]=='LONG' else -1 if bias[i]=='SHORT' else 0
            if not side or any(x[0]!=side for x in current):return None
            valid=[x for source in ('SMC_FVG_BOS','PriceAction') if (x:=methods[source].get(i)) and x[0]==side]
            if not valid:return None
        stop=min(x[1] for x in valid) if side==1 else max(x[1] for x in valid)
        target=min(x[2] for x in valid) if side==1 else max(x[2] for x in valid)
        return (side,stop,target)
    eligible=[i for i in range(len(one)-1) if i>=79 and aligned[i]>=59 and start<=one[i+1].t<end]
    if not eligible:raise RuntimeError('Not enough complete aligned bars')
    out={}
    slip=slippage_bps/10000
    for name in NAMES:
        equity=100000.;pos=None;trades=[];marks=[];rejected=signals_count=0
        for i in eligible:
            bar=one[i+1]
            if pos is None:
                event=choose(name,i)
                if event:
                    signals_count+=1
                    rr=risk(event,bar.o,equity,fee,slip)
                    if rr:
                        pos={'e':event,'entry':rr[0],'qty':rr[1],'start':bar.t,'signal':one[i].t+HOUR}
                    else:rejected+=1
            if pos:
                result=exitbar(pos['e'],bar,pos['start'],slip)
                if result:
                    price,why=result;side=pos['e'][0]
                    gross=(price-pos['entry'])*side*pos['qty']
                    net=gross-fee*(price+pos['entry'])*pos['qty']
                    equity+=net
                    trades.append({'signal_at':iso(pos['signal']),'entry_at':iso(pos['start']),
                                   'exit_bar_at':iso(bar.t+HOUR),'side':'LONG' if side==1 else 'SHORT',
                                   'net_usdt':round(net,2),'reason':why})
                    pos=None
            mark=equity
            if pos:
                side=pos['e'][0];fill=bar.c*(1-side*slip)
                mark+=((fill-pos['entry'])*side-fee*(fill+pos['entry']))*pos['qty']
            marks.append(mark)
        if pos:
            bar=one[eligible[-1]+1];side=pos['e'][0]
            fill=bar.c*(1-side*slip)
            net=((fill-pos['entry'])*side-fee*(fill+pos['entry']))*pos['qty']
            equity+=net
            trades.append({'signal_at':iso(pos['signal']),'entry_at':iso(pos['start']),
                           'exit_bar_at':iso(bar.t+HOUR),'side':'LONG' if side==1 else 'SHORT',
                           'net_usdt':round(net,2),'reason':'END_OF_TEST'})
            marks[-1]=equity
        peak=100000.;dd=0.
        for x in marks:peak=max(peak,x);dd=max(dd,(peak-x)/peak*100)
        wins=[x['net_usdt'] for x in trades if x['net_usdt']>0]
        losses=[x['net_usdt'] for x in trades if x['net_usdt']<0]
        out[name]={'trades':len(trades),'signals_when_flat':signals_count,'risk_rejected':rejected,
                   'net_usdt':round(equity-100000,2),'return_pct':round((equity/100000-1)*100,3),
                   'max_dd_pct_hourly':round(dd,3),'win_rate_pct':round(100*len(wins)/len(trades),2) if trades else None,
                   'profit_factor':round(sum(wins)/-sum(losses),3) if losses else None,
                   'trades_detail':trades}
    return {'start':iso(one[eligible[0]+1].t),'end':iso(one[eligible[-1]+1].t+HOUR),
            'eligible_hours':len(eligible),'agents':out}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--symbol',default='BTC-USDT')
    ap.add_argument('--start',default='2025-11-23')
    ap.add_argument('--end',default='2026-11-23')
    ap.add_argument('--output',default='research/results/year_backtest.json')
    args=ap.parse_args()
    if args.symbol not in ('BTC-USDT','ETH-USDT','SOL-USDT'):ap.error('Unsupported pair')
    start,end=stamp(args.start),stamp(args.end)
    if end<=start or end>int(time.time()*1000)//HOUR*HOUR:ap.error('Invalid/not closed period')
    warmup=start-30*24*HOUR
    a=fetch(args.symbol,'1h',warmup,end,HOUR)
    b=fetch(args.symbol,'4h',warmup,end,FOUR)
    checksum=hashlib.sha256(json.dumps([(x.t,x.o,x.h,x.l,x.c,x.v) for x in a],separators=(',',':')).encode()).hexdigest()
    full=analyze(a,b,start,end)
    boundary=start+int((end-start)//HOUR*.70)*HOUR
    train=analyze(a,b,start,boundary)
    oos=analyze(a,b,boundary,end)
    report={'project':'Trading AI Multi-Agent v1.2','source':'BingX public USDT perpetual REST',
            'symbol':args.symbol,'period_requested':[args.start,args.end],
            'downloaded_at_utc':dt.datetime.now(UTC).isoformat(),
            'data_1h':len(a),'data_4h':len(b),'sha256_1h':checksum,
            'model':{'initial_equity':100000,'fee_per_side':.0005,'slippage_bps_per_side':2,
                     'risk_fraction':.005,'net_rr_min':2,'funding_included':False,
                     'live_trading_enabled':False,'position_model':'single per strategy',
                     'notes':['Research only; no execution API','No liquidation model',
                              '4H EMA context is a research proxy, not full TDA 1M/1W logic',
                              'Supervisor_Context was informed by earlier five-day results']},
            'full':full,'train':train,'out_of_sample':oos}
    import os
    os.makedirs(os.path.dirname(args.output) or '.',exist_ok=True)
    with open(args.output,'w',encoding='utf8') as f:json.dump(report,f,indent=2,ensure_ascii=False)
    for segment in ('full','train','out_of_sample'):
        print('SEGMENT',segment,report[segment]['start'],report[segment]['end'])
        for name,metric in report[segment]['agents'].items():
            print(name, {k:v for k,v in metric.items() if k!='trades_detail'})
    print('REPORT',args.output,'SHA256',checksum)

if __name__=='__main__': main()
