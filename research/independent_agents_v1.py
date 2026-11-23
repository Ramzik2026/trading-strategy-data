#!/usr/bin/env python3
"""Five independent closed-bar futures signal models with conservative replay.

No live trading. No model training or parameter tuning on holdout.
Historical funding is not included, and 1H OHLC has ambiguous intrabar
SL/TP ordering: stop is always assumed first.
"""
import argparse
import hashlib
import json
import math
import time
from pathlib import Path
from year_backtest import (HOUR,FOUR,ema,atr,fetch,risk,exitbar,stamp,iso)

ASSETS=('BTC-USDT','ETH-USDT','SOL-USDT')
AGENTS=('RSI14_Reversal','MACD_Zero','Donchian55','Volume_Breakout','SNR_Retest_3x3')

def rsi_wilder(closes,n=14):
    out=[None]*len(closes)
    if len(closes)<=n:return out
    gains=[max(0,closes[i]-closes[i-1]) for i in range(1,len(closes))]
    losses=[max(0,closes[i-1]-closes[i]) for i in range(1,len(closes))]
    ag=sum(gains[:n])/n;al=sum(losses[:n])/n
    out[n]=100 if al==0 else 100-100/(1+ag/al)
    for i in range(n+1,len(closes)):
        ag=(ag*(n-1)+gains[i-1])/n
        al=(al*(n-1)+losses[i-1])/n
        out[i]=100 if al==0 else 100-100/(1+ag/al)
    return out

def macd(closes):
    fast=ema(closes,12);slow=ema(closes,26)
    line=[None if fast[i] is None or slow[i] is None else fast[i]-slow[i]
          for i in range(len(closes))]
    vals=[x for x in line if x is not None]
    sig=ema(vals,9)
    aligned=[None]*len(closes)
    for j,value in enumerate(sig):
        if value is not None:aligned[j+25]=value
    return line,aligned

def detect(bars):
    closes=[b.c for b in bars]
    v=[b.v for b in bars]
    a=atr(bars,14)
    rsi=rsi_wilder(closes,14)
    m,sig=macd(closes)
    result={name:{} for name in AGENTS}
    pivotH=pivotL=None
    armedL=armedS=None
    for i,b in enumerate(bars):
        if i<210:continue
        span=a[i]
        if span is None or span<=0:continue
        # Independent oscillator mean reversion: RSI exits oversold/overbought.
        if rsi[i-1] is not None:
            if rsi[i-1]<30<=rsi[i]:
                st=b.c-2*span;tp=b.c+4*span
                if st>0:result['RSI14_Reversal'][i]=(1,st,tp)
            elif rsi[i-1]>70>=rsi[i]:
                st=b.c+2*span;tp=b.c-4*span
                if tp>0:result['RSI14_Reversal'][i]=(-1,st,tp)
        # MACD zero cross. Only previously closed and current closed values.
        if m[i-1] is not None:
            if m[i-1]<=0<m[i]:
                st=b.c-2*span
                if st>0:result['MACD_Zero'][i]=(1,st,b.c+5*span)
            elif m[i-1]>=0>m[i]:
                st=b.c+2*span;tp=b.c-5*span
                if tp>0:result['MACD_Zero'][i]=(-1,st,tp)
        # Donchian 55: signal on a newly broken prior closed range.
        hi=max(x.h for x in bars[i-55:i])
        lo=min(x.l for x in bars[i-55:i])
        oldhi=max(x.h for x in bars[i-56:i-1])
        oldlo=min(x.l for x in bars[i-56:i-1])
        if b.c>hi and bars[i-1].c<=oldhi:
            st=b.c-2*span
            if st>0:result['Donchian55'][i]=(1,st,b.c+6*span)
        elif b.c<lo and bars[i-1].c>=oldlo:
            st=b.c+2*span;tp=b.c-6*span
            if tp>0:result['Donchian55'][i]=(-1,st,tp)
        # Volume breakout: directional 20-bar range and high volume.
        hi20=max(x.h for x in bars[i-20:i])
        lo20=min(x.l for x in bars[i-20:i])
        vol20=sum(v[i-20:i])/20
        if vol20>0 and b.v>1.5*vol20:
            if b.c>hi20:
                st=b.c-2*span
                if st>0:result['Volume_Breakout'][i]=(1,st,b.c+5*span)
            elif b.c<lo20:
                st=b.c+2*span;tp=b.c-5*span
                if tp>0:result['Volume_Breakout'][i]=(-1,st,tp)
        # Confirmation of pivot 3/3 at close of right-hand 3rd candle.
        center=i-3
        window=bars[i-6:i+1]
        if center>=3 and len(window)==7:
            ph=bars[center].h
            pl=bars[center].l
            if ph==max(x.h for x in window) and sum(x.h==ph for x in window)==1:
                pivotH=ph
            if pl==min(x.l for x in window) and sum(x.l==pl for x in window)==1:
                pivotL=pl
        # First breakout arms SNR; a later wick retest with directional close triggers.
        if armedL is not None:
            level,born=armedL
            if i-born>12 or b.c<level-.5*span:armedL=None
            elif i>born and b.l<=level and b.c>level and b.c>b.o:
                st=min(b.l,level-.5*span)
                if st>0 and st<b.c:
                    result['SNR_Retest_3x3'][i]=(1,st,b.c+2.5*(b.c-st))
                armedL=None
        if armedS is not None:
            level,born=armedS
            if i-born>12 or b.c>level+.5*span:armedS=None
            elif i>born and b.h>=level and b.c<level and b.c<b.o:
                st=max(b.h,level+.5*span)
                tp=b.c-2.5*(st-b.c)
                if tp>0 and st>b.c:result['SNR_Retest_3x3'][i]=(-1,st,tp)
                armedS=None
        # Newly broken pivot only when previously below/above and current beyond.
        if pivotH and bars[i-1].c<=pivotH<b.c:armedL=(pivotH,i)
        if pivotL and bars[i-1].c>=pivotL>b.c:armedS=(pivotL,i)
    return result

def replay(bars,signals,start,end,fee=.0005,slip=.0002):
    equity=100000.;position=None;trades=[];censored=0;reject=0;signalcount=0
    peak=100000.;dd=0.
    for i in range(1,len(bars)):
        b=bars[i]
        if b.t<start:continue
        if b.t>=end:break
        if position is None and i-1 in signals:
            signalcount+=1
            event=signals[i-1]
            qty=risk(event,b.o,equity,fee,slip)
            if qty is None:reject+=1
            else:
                position={'e':event,'entry':qty[0],'qty':qty[1],
                          'start':b.t,'signal':bars[i-1].t+HOUR}
        if position is not None:
            result=exitbar(position['e'],b,position['start'],slip)
            if result is not None:
                px,why=result
                side=position['e'][0]
                net=((px-position['entry'])*side-fee*(px+position['entry']))*position['qty']
                equity+=net
                trades.append({'start':iso(position['start']),'signal':iso(position['signal']),
                               'end':iso(b.t+HOUR),'side':side,'net_ex_funding_usdt':round(net,2),
                               'reason':why})
                position=None
        mark=equity
        if position:
            side=position['e'][0]
            px=b.c*(1-side*slip)
            mark+=((px-position['entry'])*side-fee*(px+position['entry']))*position['qty']
        peak=max(peak,mark)
        dd=max(dd,(peak-mark)/peak*100)
    if position:censored=1  # do not pretend that remaining position exited
    wins=[x['net_ex_funding_usdt'] for x in trades if x['net_ex_funding_usdt']>0]
    losses=[x['net_ex_funding_usdt'] for x in trades if x['net_ex_funding_usdt']<0]
    return {'closed_trades':len(trades),'censored':censored,
            'pnl_ex_funding_usdt':round(equity-100000,2),
            'return_ex_funding_pct':round((equity/100000-1)*100,3),
            'profit_factor_ex_funding':round(sum(wins)/-sum(losses),3) if losses else None,
            'win_rate_pct':round(len(wins)*100/len(trades),2) if trades else None,
            'max_intrahour_drawdown_pct':round(dd,3),
            'attempted_while_flat':signalcount,'risk_rejected':reject,
            'trades':trades}

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--symbol',default='BTC-USDT')
    p.add_argument('--start',default='2025-11-23')
    p.add_argument('--end',default='2026-11-23')
    p.add_argument('--output',default='research/results/independent_agents.json')
    args=p.parse_args()
    if args.symbol not in ASSETS:p.error('Unapproved pair')
    start,end=stamp(args.start),stamp(args.end)
    if not start<end<=int(time.time()*1000)//HOUR*HOUR:p.error('Bad range')
    bars=fetch(args.symbol,'1h',start-20*24*HOUR,end,HOUR)
    signals=detect(bars)
    split1=start+int((end-start)//HOUR*.6)*HOUR
    split2=start+int((end-start)//HOUR*.8)*HOUR
    segments=[('full',start,end),('dev_60',start,split1),
              ('validation_20',split1,split2),('holdout_20',split2,end)]
    result={'version':'independent-agents-year v1.0',
            'asset':args.symbol,'window':[args.start,args.end],
            'data_count_1h':len(bars),'executions_enabled':False,
            'cost':{'fee_each_side':.0005,'slippage_each_side':.0002,
                    'funding_included':False},
            'warnings':['Exploratory fixed parameters, not any established profitable system.',
                        'Last 20% already inspected by prior research: not a pristine holdout.',
                        'In-sample/hourly OHLC with ambiguous ordering; stop-first policy.',
                        'No funding or liquidation modelling.'],
            'signal_counts':{n:len(s) for n,s in signals.items()},
            'agents':{}}
    for name,ev in signals.items():
        res={}
        for seg,lo,hi in segments:
            res[seg]=replay(bars,ev,lo,hi)
        result['agents'][name]=res
        print('AGENT',args.symbol,name,'signals',len(ev),
              'trades',res['full']['closed_trades'],
              'full_pct',res['full']['return_ex_funding_pct'],
              'pf',res['full']['profit_factor_ex_funding'],
              'dev_pct',res['dev_60']['return_ex_funding_pct'],
              'validation_pct',res['validation_20']['return_ex_funding_pct'],
              'holdout_pct',res['holdout_20']['return_ex_funding_pct'],
              flush=True)
    p=Path(args.output);p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    print('RESULT',p,flush=True)

if __name__=='__main__':main()
