#!/usr/bin/env python3
"""TDA v1.3 separate-strategy historical evaluation.
Read-only research. Excludes RB until 15m confirmation is built.
"""
import argparse
import json
import time
from pathlib import Path
from year_backtest import (HOUR, FOUR, risk,exitbar,fetch,stamp,iso)
from tda_v13 import get_htf,hourly_signals,DAY

def simulate(bars,signals,start,end,fee=.0005,slip=.0002):
    equity=100000.; pos=None; trades=[]; rejected=0; eligible=0
    marks=[]
    for i in range(len(bars)-1):
        next_bar=bars[i+1]
        if not(start<=next_bar.t<end):continue
        if not pos and i in signals:
            eligible+=1
            e=signals[i]
            order=risk(e,next_bar.o,equity,fee,slip)
            if order:
                pos={'event':e,'entry':order[0],'qty':order[1],
                     'time':next_bar.t,'signal':bars[i].t+HOUR}
            else:rejected+=1
        if pos:
            hit=exitbar(pos['event'],next_bar,pos['time'],slip)
            if hit:
                px,reason=hit
                side=pos['event'][0]
                net=((px-pos['entry'])*side-fee*(px+pos['entry']))*pos['qty']
                equity+=net
                trades.append({'signal':iso(pos['signal']),'exit':iso(next_bar.t+HOUR),
                               'side':'LONG' if side==1 else 'SHORT',
                               'pnl_usdt':round(net,2),'reason':reason})
                pos=None
        mark=equity
        if pos:
            b=next_bar;side=pos['event'][0];fill=b.c*(1-side*slip)
            mark+=((fill-pos['entry'])*side-fee*(fill+pos['entry']))*pos['qty']
        marks.append(mark)
    if pos:
        bar=bars[-1];side=pos['event'][0];fill=bar.c*(1-side*slip)
        net=((fill-pos['entry'])*side-fee*(fill+pos['entry']))*pos['qty']
        equity+=net
        trades.append({'signal':iso(pos['signal']),'exit':iso(bar.t+HOUR),
                       'side':'LONG' if side==1 else 'SHORT','pnl_usdt':round(net,2),
                       'reason':'END_OF_TEST'})
        if marks:marks[-1]=equity
    peak=100000.;drawdown=0.
    for x in marks:
        peak=max(peak,x);drawdown=max(drawdown,100*(peak-x)/peak)
    wins=[x['pnl_usdt'] for x in trades if x['pnl_usdt']>0]
    losses=[x['pnl_usdt'] for x in trades if x['pnl_usdt']<0]
    return {'trades':len(trades),'signals_while_flat':eligible,'risk_rejected':rejected,
            'net_usdt':round(equity-100000,2),'return_pct':round((equity/100000-1)*100,3),
            'max_drawdown_pct_hourly':round(drawdown,3),
            'profit_factor':round(sum(wins)/-sum(losses),3) if losses else None,
            'win_rate_pct':round(len(wins)/len(trades)*100,2) if trades else None,
            'trades_detail':trades}

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--symbol',default='BTC-USDT')
    p.add_argument('--start',default='2025-11-23')
    p.add_argument('--end',default='2026-11-23')
    p.add_argument('--output',default='research/results/tda_v13_backtest.json')
    a=p.parse_args()
    if a.symbol not in ('BTC-USDT','ETH-USDT','SOL-USDT'):p.error('Unsupported symbol')
    start,end=stamp(a.start),stamp(a.end)
    if not start<end<=int(time.time()*1000)//HOUR*HOUR:p.error('Invalid dates')
    one=fetch(a.symbol,'1h',start-30*DAY,end,HOUR)
    four=fetch(a.symbol,'4h',start-30*DAY,end,FOUR)
    month=get_htf(a.symbol,'1M',stamp('2022-01-01'),end)
    week=get_htf(a.symbol,'1w',stamp('2022-01-01'),end)
    series,reasons,stats,bias=hourly_signals(one,four,month,week)
    signals={n:series[n] for n in ('FVG','SNR')}
    # Coordinator is pure rules, not an LLM: when both same direction on a bar,
    # use the wider protective stop and more conservative target.
    combined={}
    for i in sorted(set(signals['FVG'])&set(signals['SNR'])):
        f=signals['FVG'][i];s=signals['SNR'][i]
        if f[0]!=s[0]:continue
        d=f[0]
        st=min(f[1],s[1]) if d==1 else max(f[1],s[1])
        tar=min(f[2],s[2]) if d==1 else max(f[2],s[2])
        combined[i]=(d,st,tar)
    signals['Supervisor_strict']=combined
    # No vote requirement: allow only when exactly one module emits an idea,
    # otherwise veto on conflict or both to avoid duplicating a setup.
    context={}
    for i in sorted(set(signals['FVG'])|set(signals['SNR'])):
        active=[v[i] for v in signals.values() if i in v]
        if len(active)==1:context[i]=active[0]
    signals['Supervisor_single']=context
    boundary=start+int((end-start)//HOUR*.7)*HOUR
    output={'version':'v1.3 research, provisional rules','symbol':a.symbol,'period':[a.start,a.end],
            'data_counts':{'1h':len(one),'4h':len(four),'1M':len(month),'1w':len(week)},
            'notional_start_usdt':100000,'risk_fraction':0.005,
            'fee_side':0.0005,'slippage_side':0.0002,
            'funding_included':False,'trade_execution_enabled':False,
            'limitations':['FVG/SNR definitions exploratory, not proven identical to full TDA',
                           'RB excluded until proper 15m confirmation',
                           'No intrabar order book, liquidation, funding or real liquidity simulation',
                           'Supervisor_single is an exploratory rules aggregator, not AI',
                           'No independent untouched final holdout yet'],
            'signal_count':{k:len(v) for k,v in signals.items()},
            'segments':{}}
    for name,lo,hi in [('full',start,end),('early_70pct',start,boundary),('late_30pct',boundary,end)]:
        output['segments'][name]={k:simulate(one,s,lo,hi) for k,s in signals.items()}
    target=Path(a.output);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(output,indent=2,ensure_ascii=False),encoding='utf8')
    print('SOURCE',output['data_counts'])
    print('SIGNAL_COUNT',output['signal_count'])
    for name,seg in output['segments'].items():
        print('SEGMENT',name)
        for alg,d in seg.items():
            print(alg,{k:v for k,v in d.items() if k!='trades_detail'})
    print('REPORT',target)

if __name__=='__main__':main()
