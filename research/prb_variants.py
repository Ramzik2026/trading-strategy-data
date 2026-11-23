#!/usr/bin/env python3
"""Predeclared PRB research arms: 1/1 vs 3/3 4H fractal,
4/8/12h TTL, 15m breakout vs zone-reclaim trigger.

This is exploratory parameter sensitivity, not selection of a winner.
Chronological splits 60% development, 20% validation, 20% contaminated
holdout (we already inspected these dates). Funding is excluded here.
"""
import argparse
import bisect
import datetime as dt
import hashlib
import json
import time
from pathlib import Path
from year_backtest import Bar,fetch,stamp,iso,FOUR
from prb_v15_engine import (Q,D,aggregate,contracts,frac_levels,detect_pairs,replay)

def fractal_right3(four,quarter,tick):
    """Price pivot 3/3, known only after closing the 3rd right-hand bar."""
    times=[x.t for x in quarter]
    levels=[]
    left=right=3
    for i in range(left,len(four)-right):
        center=four[i]
        window=four[i-left:i+right+1]
        for side,x,valid in [
          (1,center.l,center.l==min(b.l for b in window) and sum(b.l==center.l for b in window)==1),
          (-1,center.h,center.h==max(b.h for b in window) and sum(b.h==center.h for b in window)==1),
        ]:
            if not valid:continue
            confirmed=four[i+right].t+FOUR
            start=bisect.bisect_left(times,confirmed)
            last=min(len(quarter),start+180*16)
            swept=None
            for b in quarter[start:last]:
                if (side==1 and b.l<=x-tick*.5) or (side==-1 and b.h>=x+tick*.5):
                    swept=b.t+Q
                    break
            levels.append({'side':side,'price':x,'center_t':center.t,
                           'confirmed':confirmed,'expiry':center.t+181*FOUR,
                           'swept':swept,'id':str(center.t)+'_'+('LOW' if side==1 else 'HIGH')})
    return sorted(levels,key=lambda z:(z['confirmed'],z['id']))

def aligned_split(start,end):
    total=(end-start)//Q
    a=start+int(total*.6)*Q
    b=start+int(total*.8)*Q
    return [('full',start,end),('development_60pct',start,a),
            ('validation_20pct',a,b),('holdout_20pct',b,end)]

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--symbol',required=True)
    p.add_argument('--start',default='2026-09-25')
    p.add_argument('--end',default='2026-11-23')
    p.add_argument('--output',required=True)
    args=p.parse_args()
    if args.symbol not in ('BTC-USDT','ETH-USDT','SOL-USDT'):p.error('Symbol not supported')
    start,end=stamp(args.start),stamp(args.end)
    if end<=start or end>int(time.time()*1000)//Q*Q:p.error('Invalid start/end')
    quarter=fetch(args.symbol,'15m',start-35*D,end,Q)
    one=aggregate(quarter,4)
    four=aggregate(quarter,16)
    cfg=contracts(args.symbol)
    result={
        'project':'Trading AI PRB research v1.5',
        'symbol':args.symbol,'window':[args.start,args.end],
        'data':{'15m':len(quarter),'1h':len(one),'4h':len(four)},
        'split_info':'Exploratory (NOT an untouched independent holdout after seeing first RB results)',
        'funding_included':False,'live_trading':False,
        'censored_final_positions_are_excluded':True,
        'scenarios':{}
    }
    for pivot in ('pivot_1_1','pivot_3_3'):
        levels=frac_levels(four,quarter,cfg['tick']) if pivot=='pivot_1_1' else fractal_right3(four,quarter,cfg['tick'])
        pairs,stats=detect_pairs(one,levels,cfg['tick'],start,end)
        for ttl in (4,8,12):
            zz=[dict(z,expiry=z['ready']+ttl*3600000) for z in pairs]
            for trigger in ('break','reclaim'):
                key=f'{pivot}_TTL{ttl}_{trigger}'
                pieces={}
                for seg,begin,stop in aligned_split(start,end):
                    d=replay(quarter,zz,cfg,begin,stop,fee=.0006,slip=.0002,trigger_mode=trigger)
                    pieces[seg]={k:v for k,v in d.items() if k!='trades'}
                result['scenarios'][key]={
                    'pivot':pivot,'ttl_hours':ttl,'trigger':trigger,
                    'levels':len(levels),'rb_ready':len(pairs),
                    'segments':pieces
                }
                full=pieces['full'];hold=pieces['holdout_20pct']
                print('ARM',args.symbol,key,'N',full['closed_trades'],
                      'FULL',full['return_ex_funding_pct'],
                      'DEV',pieces['development_60pct']['return_ex_funding_pct'],
                      'VALID',pieces['validation_20pct']['return_ex_funding_pct'],
                      'HOLD',hold['return_ex_funding_pct'],
                      'CENSORED',full['censored_open_positions'],flush=True)
    target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print('REPORT',args.symbol,target,flush=True)

if __name__=='__main__':main()
