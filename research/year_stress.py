#!/usr/bin/env python3
"""Cost sensitivity and timeframe integrity audit, research only."""
import argparse, datetime as dt, hashlib, json, time
from pathlib import Path
from year_backtest import (HOUR, FOUR, UTC, fetch, analyze, stamp, iso)

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--symbol',default='BTC-USDT')
    p.add_argument('--start',default='2025-11-23')
    p.add_argument('--end',default='2026-11-23')
    p.add_argument('--output',default='research/results/year_stress.json')
    a=p.parse_args()
    assert a.symbol in ('BTC-USDT','ETH-USDT','SOL-USDT')
    start,end=stamp(a.start),stamp(a.end)
    assert start<end<=int(time.time()*1000)//HOUR*HOUR
    warm=start-30*24*HOUR
    one=fetch(a.symbol,'1h',warm,end,HOUR)
    four=fetch(a.symbol,'4h',warm,end,FOUR)
    by_t={b.t:b for b in one}
    comparisons=0
    for f in four:
        group=[by_t.get(f.t+j*HOUR) for j in range(4)]
        if any(b is None for b in group): continue
        comparisons+=1
        if (abs(f.o-group[0].o)>1e-6 or abs(f.c-group[-1].c)>1e-6
            or abs(f.h-max(b.h for b in group))>1e-6
            or abs(f.l-min(b.l for b in group))>1e-6):
            raise RuntimeError('4H/1H price data mismatch at '+iso(f.t))
    if comparisons<100:raise RuntimeError('Too few 1H/4H consistency checks')
    boundaries=start+int((end-start)//HOUR*0.7)*HOUR
    scenarios={
      'baseline_5bps_fee_2bps_slippage':(.0005,2),
      'stress_10bps_fee_5bps_slippage':(.001,5),
      'severe_15bps_fee_10bps_slippage':(.0015,10)
    }
    report={'project':'Trading AI Multi-Agent v1.2 cost sensitivity',
            'symbol':a.symbol,'window':[a.start,a.end],
            'downloaded_utc':dt.datetime.now(UTC).isoformat(),
            'one_hour_bars':len(one),'four_hour_bars':len(four),
            'matching_4h_aggregates':comparisons,
            'funding_included':False,'live_trading_enabled':False,
            'assumptions':'Separate $100k hypothetical portfolios; next-hour-open fill; stop-first ambiguous bars',
            'scenarios':{}}
    for name,(fee,slip) in scenarios.items():
        full=analyze(one,four,start,end,fee,slip)
        oos=analyze(one,four,boundaries,end,fee,slip)
        report['scenarios'][name]={
            'fees_each_side':fee,'slippage_each_side_bps':slip,
            'full':{k:{x:y for x,y in v.items() if x!='trades_detail'} for k,v in full['agents'].items()},
            'out_of_sample':{k:{x:y for x,y in v.items() if x!='trades_detail'} for k,v in oos['agents'].items()}
        }
    target=Path(a.output);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print('CHECKED 4H aggregates',comparisons)
    for name,scenario in report['scenarios'].items():
        print('SCENARIO',name)
        for agent,metrics in scenario['full'].items():
            print(agent,metrics['trades'],metrics['return_pct'],metrics['profit_factor'])
    print('REPORT',target)

if __name__=='__main__':main()
