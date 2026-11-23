#!/usr/bin/env python3
"""PRB research v1.4: read-only Pixel RB 4H fractal -> 1H pair -> 15m retest.

Primary rulebook: Pixel_RB_Test_Spec_v1_0.docx.  Costs and timestamps are
explicit assumptions; realized historical funding is NOT yet integrated.
No API key, no order endpoint, no real/de­mo trading.
"""
import argparse
import bisect
import collections
import datetime as dt
import hashlib
import json
import math
import time
import urllib.parse
import urllib.request
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

from year_backtest import Bar, fetch, stamp, iso, HOUR, FOUR, UTC

Q=15*60*1000
D=24*HOUR
W=7*D
API_ROOT='https://open-api.bingx.com'
RISK_FRAC=.005
MAX_NOTIONAL=3
FUNDING_RESERVE=.001  # entry screening only; never a realized ledger charge

def contracts(symbol):
    p=urllib.parse.urlencode({'symbol':symbol})
    req=urllib.request.Request(API_ROOT+'/openApi/swap/v2/quote/contracts?'+p,
                               headers={'User-Agent':'Trading-AI-PRB-Research/1.4'})
    time.sleep(1.15)
    with urllib.request.urlopen(req,timeout=30) as f: response=json.load(f)
    if str(response.get('code'))!='0': raise RuntimeError('Contract API '+str(response))
    rows=response['data']
    if not isinstance(rows,list):rows=[rows]
    matches=[x for x in rows if x['symbol']==symbol]
    if len(matches)!=1:raise RuntimeError('Contract missing or duplicated: '+symbol)
    c=matches[0]
    if c.get('currency')!='USDT' or int(c.get('status',0))!=1:
        raise RuntimeError('Inactive or non-USDT linear contract')
    tick=10**(-int(c['pricePrecision']))
    step=10**(-int(c['quantityPrecision']))
    return {'symbol':symbol,'tick':tick,'step':step,
            'minimum_qty':float(c['tradeMinQuantity']),
            'minimum_notional':float(c['tradeMinUSDT']),
            'current_contract_as_historical_proxy':True,
            'maker_fee_now':c.get('makerFeeRate'),
            'taker_fee_now':c.get('takerFeeRate')}

def aggregate(bars,k):
    if len(bars)%k:raise ValueError('Aggregate requires complete groups')
    result=[]
    for i in range(0,len(bars),k):
        g=bars[i:i+k]
        if any(g[j].t!=g[0].t+j*Q for j in range(k)):raise ValueError('Gap')
        if g[0].t%(k*Q):raise ValueError('Misaligned bucket '+iso(g[0].t))
        result.append(Bar(g[0].t,g[0].o,max(x.h for x in g),
                          min(x.l for x in g),g[-1].c,sum(x.v for x in g)))
    return result

def frac_levels(four,quarter,tick):
    """1/1 pivots; become available only after right 4H candle closes.
    Precompute first future sweep but NEVER use it unless its timestamp <= snapshot.
    This equivalence is tested with prefix invariance tests.
    """
    timestamps=[x.t for x in quarter]
    levels=[]
    for i in range(1,len(four)-1):
        p,mid,n=four[i-1:i+2]
        for side,yes,value in (
            (1,mid.l<p.l and mid.l<n.l,mid.l),
            (-1,mid.h>p.h and mid.h>n.h,mid.h)):
            if not yes:continue
            confirmed=n.t+FOUR
            start=bisect.bisect_left(timestamps,confirmed)
            # limit to 180 4H periods; level cannot be used after expiration
            last=min(len(quarter),start+180*16)
            swept=None
            for q in quarter[start:last]:
                if (side==1 and q.l<=value-tick*0.5) or (side==-1 and q.h>=value+tick*0.5):
                    swept=q.t+Q;break
            levels.append({'side':side,'price':value,'center_t':mid.t,
                           'confirmed':confirmed,'expiry':mid.t+181*FOUR,
                           'swept':swept,'id':f"{mid.t}_{'LOW' if side==1 else 'HIGH'}"})
    return sorted(levels,key=lambda x:(x['confirmed'],x['id']))

def detect_pairs(one,levels,tick,start,end):
    """Create candidates only using pre-existing snapshot at C1 open."""
    confirmations=[v['confirmed'] for v in levels]
    rows=[];stats=collections.Counter()
    for i in range(1,len(one)):
        c1,c2=one[i-1:i+1]
        if not start-8*HOUR<=c2.t+HOUR<end:continue
        side=1 if c1.c<c1.o and c2.c>c2.o else -1 if c1.c>c1.o and c2.c<c2.o else 0
        if not side:continue
        stats['pair_colored']+=1
        if abs(c1.c-c2.o)>tick*.500001:
            stats['RB_GAP']+=1;continue
        snapshot=[z for z in levels[:bisect.bisect_right(confirmations,c1.t)]
                  if z['center_t']+180*FOUR>=c1.t and (z['swept'] is None or z['swept']>c1.t)]
        selected=[]
        for level in snapshot:
            L=level['price']
            if level['side']!=side:continue
            if side==1:
                if L>=c1.o or min(c1.l,c2.l)>L-tick*.5:continue
                rejection=any(x.l<=L-tick*.5 and min(x.o,x.c)>=L-tick*.001 for x in (c1,c2))
                if not rejection or c2.c<L+tick*.5:continue
            else:
                if L<=c1.o or max(c1.h,c2.h)<L+tick*.5:continue
                rejection=any(x.h>=L+tick*.5 and max(x.o,x.c)<=L+tick*.001 for x in (c1,c2))
                if not rejection or c2.c>L-tick*.5:continue
            selected.append(level)
        if not selected:
            stats['NO_LEVEL_OR_REJECTION']+=1;continue
        # Low: choose lowest swept. High: choose highest swept.
        source=min(selected,key=lambda z:(z['price'],z['id'])) if side==1 else max(selected,key=lambda z:(z['price'],z['id']))
        targets=[z for z in snapshot if z['side']==-side and
                 (z['price']>c1.o if side==1 else z['price']<c1.o)]
        if not targets:stats['NO_TARGET']+=1;continue
        target=min(targets,key=lambda z:(z['price'],z['id'])) if side==1 else max(targets,key=lambda z:(z['price'],z['id']))
        if (side==1 and max(c1.h,c2.h)>=target['price']-tick*.5 or
            side==-1 and min(c1.l,c2.l)<=target['price']+tick*.5):
            stats['TARGET_REACHED_DURING_FORMATION']+=1;continue
        low=min(c1.l,c2.l) if side==1 else c1.c
        high=c1.c if side==1 else max(c1.h,c2.h)
        if high-low<tick*.5:stats['RB_ZERO_WIDTH']+=1;continue
        stop=low-tick if side==1 else high+tick
        take=target['price']-tick if side==1 else target['price']+tick
        if not (stop<take if side==1 else take<stop):
            stats['INVALID_GEOMETRY']+=1;continue
        ready=c2.t+HOUR
        rows.append({'id':f"{c1.t}_{side}_{source['id']}",'side':side,'ready':ready,
                     'expiry':ready+8*HOUR,'lo':low,'hi':high,'stop':stop,'tp':take,
                     'level':source['price'],'target':target['price'],
                     'source_id':source['id'],'target_id':target['id'],
                     'c1':c1.t,'c2':c2.t})
        stats['RB_READY']+=1
    rows.sort(key=lambda z:(z['ready'],z['id']))
    return rows,stats

def fee_risk(side,op,stop,tp,fee,slip,reserve):
    entry=op*(1+side*slip)
    sx=stop*(1-side*slip)
    tx=tp*(1-side*slip)
    cost_entry=fee*entry
    risk_unit=side*(entry-sx)+cost_entry+fee*sx+reserve*entry
    reward_unit=side*(tx-entry)-cost_entry-fee*tx-reserve*entry
    if entry<=0 or sx<=0 or tx<=0 or risk_unit<=0 or reward_unit<=0:
        return None
    if (side==1 and not stop<entry<tp) or (side==-1 and not tp<entry<stop):
        return None
    if reward_unit/risk_unit<2: return None
    return entry,risk_unit,reward_unit/risk_unit

def exit_bar(pos,bar,slip):
    side=pos['z']['side'];stop=pos['z']['stop'];tp=pos['z']['tp']
    if side==1:
        if bar.o<=stop:return bar.o*(1-slip),'STOP_GAP'
        if bar.o>=tp:return tp*(1-slip),'TARGET_GAP'
        sh=bar.l<=stop;th=bar.h>=tp
    else:
        if bar.o>=stop:return bar.o*(1+slip),'STOP_GAP'
        if bar.o<=tp:return tp*(1+slip),'TARGET_GAP'
        sh=bar.h>=stop;th=bar.l<=tp
    if sh:return stop*(1-side*slip),'SL_FIRST_AMBIGUOUS' if th else 'STOP'
    if th:return tp*(1-side*slip),'TARGET'
    if pos['bars']>=96:return bar.c*(1-side*slip),'TIME_EXIT'
    return None

def replay(quarter,pairs,cfg,start,end,variant='PRB_15m',fee=.0006,slip=.0002):
    """Single-position conservative intrabar replay. Open processing uses previous D."""
    tick=cfg['tick'];step=Decimal(str(cfg['step']))
    born=collections.defaultdict(list)
    for z in pairs:born[z['ready']].append(dict(z))
    active=[];pending=None;position=None;trades=[];reason=collections.Counter()
    eq=100000.;peak=eq;maxdd=0.
    first_eval=start
    for i,b in enumerate(quarter):
        now=b.t+Q
        # New 1H C2 pair is only available AFTER its close.
        if i==0:continue
        if b.t>=start and position:
            position['bars']+=1
            e=exit_bar(position,b,slip)
            if e:
                px,why=e;z=position['z'];q=position['qty'];entry=position['entry']
                pnl=((px-entry)*z['side']-fee*(px+entry))*q
                eq+=pnl
                trades.append({'setup':z['id'],'side':'LONG' if z['side']==1 else 'SHORT',
                               'entry_time':iso(position['entry_t']),'exit_time':iso(now),
                               'entry':round(entry,8),'exit':round(px,8),
                               'qty':q,'pnl_after_fees_ex_funding':round(pnl,2),
                               'reason':why,'R_ex_funding':round(pnl/position['initial_risk'],4)})
                reason['CLOSED_'+why]+=1
                position=None
        if b.t>=start and pending and pending['entry_t']==b.t:
            z=pending['z'];pending=None
            if position:
                reason['SKIPPED_POSITION_OPEN']+=1
            elif b.t>=z['expiry'] or (z['side']==1 and (b.o<=z['lo']-tick or b.o>=z['tp'])) or (
                    z['side']==-1 and (b.o>=z['hi']+tick or b.o<=z['tp'])):
                reason['GAP_OR_TTL']+=1
            else:
                risk=fee_risk(z['side'],b.o,z['stop'],z['tp'],fee,slip,FUNDING_RESERVE)
                if not risk:reason['RR_TOO_LOW']+=1
                else:
                    entry,per_unit,rr=risk
                    qty=min(eq*RISK_FRAC/per_unit,MAX_NOTIONAL*eq/entry)
                    qty=float((Decimal(str(qty))/step).to_integral_value(rounding=ROUND_DOWN)*step)
                    if qty<cfg['minimum_qty'] or qty*entry<cfg['minimum_notional']:
                        reason['SIZE_REJECTED']+=1
                    else:
                        position={'z':z,'entry':entry,'qty':qty,'entry_t':b.t,'bars':1,
                                  'initial_risk':per_unit*qty,'rr':rr}
                        reason['SIM_OPEN']+=1
                        # Entry bar must be evaluated for SL/TP too.
                        hit=exit_bar(position,b,slip)
                        if hit:
                            px,why=hit
                            pnl=((px-entry)*z['side']-fee*(px+entry))*qty
                            eq+=pnl
                            trades.append({'setup':z['id'],'side':'LONG' if z['side']==1 else 'SHORT',
                                           'entry_time':iso(b.t),'exit_time':iso(now),
                                           'qty':qty,'pnl_after_fees_ex_funding':round(pnl,2),
                                           'reason':why,'R_ex_funding':round(pnl/(per_unit*qty),4)})
                            reason['CLOSED_'+why]+=1
                            position=None
        if b.t>=start:
            keep=[];triggered=[]
            for z in active:
                side=z['side']
                if b.t>=z['expiry']:reason['EXPIRED']+=1;continue
                if side==1 and b.l<=z['lo']-tick*.5 or side==-1 and b.h>=z['hi']+tick*.5:
                    reason['INVALIDATED']+=1;continue
                if side==1 and b.h>=z['target']-tick*.5 or side==-1 and b.l<=z['target']+tick*.5:
                    reason['TARGET_REACHED_BEFORE_ENTRY']+=1;continue
                if now>=z['expiry']:
                    reason['EXPIRED']+=1;continue
                if b.l<=z['hi'] and b.h>=z['lo']:
                    prev=quarter[i-1]
                    confirm=b.c>prev.h if side==1 else b.c<prev.l
                    if confirm and i+1<len(quarter) and quarter[i+1].t<z['expiry']:
                        triggered.append(z);continue
                keep.append(z)
            active=keep
            if triggered:
                triggered.sort(key=lambda z:(z['ready'],z['id']))
                chosen=triggered[0]
                if not pending and not position:
                    pending={'z':chosen,'entry_t':now}
                    reason['ENTRY_PENDING']+=1
                else:reason['SKIPPED_POSITION_OPEN']+=1
                reason['SKIPPED_CONCURRENT']+=len(triggered)-1
            if position: 
                mark=eq+((b.c*(1-position['z']['side']*slip)-position['entry'])*position['z']['side']-
                         fee*(b.c+position['entry']))*position['qty']
            else: mark=eq
            peak=max(peak,mark);maxdd=max(maxdd,100*(peak-mark)/peak)
        # New pair becomes eligible for D only on NEXT candle, never C2 itself.
        for z in born.get(now,[]):
            if now<=end:
                if position:reason['SKIPPED_POSITION_OPEN']+=1
                else:active.append(z)
    if position:
        b=quarter[-1];z=position['z'];px=b.c*(1-z['side']*slip);q=position['qty']
        pnl=((px-position['entry'])*z['side']-fee*(px+position['entry']))*q
        eq+=pnl
        trades.append({'setup':z['id'],'side':'LONG' if z['side']==1 else 'SHORT',
                       'entry_time':iso(position['entry_t']),'exit_time':iso(b.t+Q),
                       'qty':q,'pnl_after_fees_ex_funding':round(pnl,2),
                       'reason':'CENSORED_FINAL_MARK','R_ex_funding':round(pnl/position['initial_risk'],4)})
        reason['CENSORED_FINAL_MARK']+=1
    wins=[t['pnl_after_fees_ex_funding'] for t in trades if t['pnl_after_fes_ex_funding']>0] if False else [
          t['pnl_after_fees_ex_funding'] for t in trades if t['pnl_after_fees_ex_funding']>0]
    losses=[t['pnl_after_fees_ex_funding'] for t in trades if t['pnl_after_fees_ex_funding']<0]
    return {'num_trades_including_censored':len(trades),'return_ex_funding_pct':round((eq/100000-1)*100,3),
            'profit_factor_ex_funding':round(sum(wins)/-sum(losses),3) if losses else None,
            'win_rate_pct':round(100*len(wins)/len(trades),2) if trades else None,
            'max_drawdown_pct_15m':round(maxdd,3),
            'reasons':dict(reason),'funding_status':'FUNDING_INCOMPLETE',
            'trades':trades}

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--symbol',default='BTC-USDT')
    p.add_argument('--start',default='2025-11-23')
    p.add_argument('--end',default='2026-11-23')
    p.add_argument('--output',default='research/results/prb_v14.json')
    a=p.parse_args()
    if a.symbol not in ('BTC-USDT','ETH-USDT','SOL-USDT'):p.error('Unsupported instrument')
    start,end=stamp(a.start),stamp(a.end)
    if not start<end<=int(time.time()*1000)//Q*Q:p.error('Invalid date range')
    warm=start-104*D
    # 15m data is canonical. Do not mix data from other exchanges.
    quarter=fetch(a.symbol,'15m',warm,end,Q)
    one=aggregate(quarter,4)
    four=aggregate(quarter,16)
    if len(quarter)<365*96 or len(four)<180:raise RuntimeError('Insufficient bars')
    cfg=contracts(a.symbol)
    levels=frac_levels(four,quarter,cfg['tick'])
    pairs,pstats=detect_pairs(one,levels,cfg['tick'],start,end)
    report={'version':'PRB research 1.4','symbol':a.symbol,'window':[a.start,a.end],
            'rules_source':'Pixel_RB_Test_Spec_v1_0.docx',
            'data_source':'BingX perpetual 15m; 1h/4h aggregated from the same feed',
            'data_integrity':{'15m':len(quarter),'1h':len(one),'4h':len(four),
                              'ohlcv_sha256':hashlib.sha256(
                                  json.dumps([(x.t,x.o,x.h,x.l,x.c,x.v) for x in quarter],
                                             separators=(',',':')).encode()).hexdigest()},
            'contract':cfg,'levels_count':len(levels),'pair_reasons':dict(pstats),
            'ready_pairs':len(pairs),'fee_scenarios':{},
            'cautions':['IDEAL_EVENT_TIME: assume candle data known exactly at close',
                        'Historical contract precision is approximated using CURRENT contract info',
                        'Historical funding rates and mark prices are NOT included',
                        'Reported returns exclude funding and are NOT economically accepted net results',
                        'No direct L2/L1 intrabar sequence; stop-first on ambiguous bars',
                        'Read-only research; trading disabled']}
    for name,fee,slip in [('base_6bps_2bps',.0006,.0002),
                          ('stress_6bps_5bps',.0006,.0005),
                          ('stress_6bps_10bps',.0006,.001)]:
        d=replay(quarter,pairs,cfg,start,end,fee=fee,slip=slip)
        report['fee_scenarios'][name]=d
        print('SCENARIO',name,'ready',len(pairs),'trades',d['num_trades_including_censored'],
              'return_ex_funding_pct',d['return_ex_funding_pct'],
              'pf_ex_funding',d['profit_factor_ex_funding'],'reasons',d['reasons'])
    dest=Path(a.output);dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print('REPORT',dest)

if __name__=='__main__':main()
