#!/usr/bin/env python3
"""Post-processing audit of PRB 1.4 result artifacts.

Isolates censored positions from realized PnL, attributes historical
BingX funding (rate * markPrice * qty) on closed trades, and reports
the 60/20/20 chronology. Does NOT recompute the original position
sizes after funding; funding-adjusted return is an approximation.
"""
import argparse
import bisect
import datetime as dt
import json
import math
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

UTC=dt.timezone.utc
DAY=86400000
URL='https://open-api.bingx.com/openApi/swap/v2/quote/fundingRate'

def iso_parse(s):
    return int(dt.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()*1000)

def funding(symbol,start,end):
    """Fetch in <=15 day windows to stay below 1000 records at hourly settlement."""
    rows={}
    for lo in range(start,end,15*DAY):
        hi=min(lo+15*DAY,end)
        query=urllib.parse.urlencode({
            'symbol':symbol,'startTime':lo,'endTime':hi-1,'limit':1000})
        req=urllib.request.Request(URL+'?'+query,
                                   headers={'User-Agent':'Trading-AI-PRB-Funding-Audit/1.0'})
        time.sleep(1.15)
        with urllib.request.urlopen(req,timeout=40) as f:r=json.load(f)
        if str(r.get('code'))!='0' or not isinstance(r.get('data'),list):
            raise RuntimeError('Bad historical funding response '+str(r.get('msg')))
        for z in r['data']:
            t=int(z['fundingTime'])
            if not lo<=t<hi:continue
            val=(float(z['fundingRate']),float(z['markPrice']))
            if not all(math.isfinite(x) for x in val) or val[1]<=0 or abs(val[0])>0.10:
                raise ValueError('Bad funding record at '+str(t))
            if t in rows and rows[t]!=val:raise ValueError('Conflicting funding')
            rows[t]=val
    return sorted((t,*v) for t,v in rows.items())

def audit(path):
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    symbol=data['symbol']
    start=iso_parse(data['window'][0]+'T00:00:00+00:00')
    end=iso_parse(data['window'][1]+'T00:00:00+00:00')
    funding_rows=funding(symbol,start,end)
    marks=[x[0] for x in funding_rows]
    gaps=[marks[i+1]-marks[i] for i in range(len(marks)-1)]
    maxgap=max(gaps,default=end-start)
    coverage=bool(marks and marks[0]-start<=12*3600000 and
                  end-marks[-1]<=12*3600000 and maxgap<=12*3600000)
    result={
      'symbol':symbol,'window':data['window'],
      'funding_rows':len(funding_rows),
      'funding_first':dt.datetime.fromtimestamp(marks[0]/1000,UTC).isoformat() if marks else None,
      'funding_last':dt.datetime.fromtimestamp(marks[-1]/1000,UTC).isoformat() if marks else None,
      'funding_max_gap_hours':round(maxgap/3600000,2),
      'funding_coverage_verified':coverage,
      'caveats':['Historical funding is attributed after the original fee-based replay. '
                 'Position sizes were not recalculated after funding. Not an execution-grade net backtest.',
                 'Daily and contract historical precision may differ from current settings.',
                 'Results are a short 15m window, not a year.'],
      'scenarios':{}
    }
    for name,scenario in data['fee_scenarios'].items():
        trades=scenario['trades']
        cens=[t for t in trades if t['reason'].startswith('CENSORED')]
        realized=[t for t in trades if not t['reason'].startswith('CENSORED')]
        adjusted=[];cost=0;missing=0
        cohorts={key:Counter() for key in ['development_60pct','validation_20pct','holdout_20pct']}
        for t in realized:
            begin=iso_parse(t['entry_time'])
            stop=iso_parse(t['exit_time'])
            side=1 if t['side']=='LONG' else -1
            qty=float(t['qty'])
            i=bisect.bisect_right(marks,begin)
            j=bisect.bisect_left(marks,stop)
            pay=sum(side*rate*mark*qty for _,rate,mark in funding_rows[i:j])
            baseline=float(t['pnl_after_fees_ex_funding'])
            adjusted.append(baseline-pay)
            cost+=pay
            k=0 if begin<start+int((end-start)*.6) else 1 if begin<start+int((end-start)*.8) else 2
            key=['development_60pct','validation_20pct','holdout_20pct'][k]
            cohorts[key]['trades']+=1
            cohorts[key]['pnl_ex_funding_usdt']+=baseline
            cohorts[key]['pnl_funding_adjusted_usdt']+=baseline-pay
            stopboundary=[start+int((end-start)*.6),start+int((end-start)*.8),end][k]
            if stop>stopboundary:cohorts[key]['crossed_segment_boundary']+=1
        wins=[x for x in adjusted if x>0];losses=[x for x in adjusted if x<0]
        result['scenarios'][name]={
          'source_trades_including_censored':len(trades),
          'closed_trades':len(realized),
          'censored_open_positions':len(cens),
          'realized_pnl_ex_funding_usdt':round(sum(float(t['pnl_after_fees_ex_funding']) for t in realized),2),
          'realized_return_ex_funding_pct':round(sum(float(t['pnl_after_fees_ex_funding']) for t in realized)/100000*100,3),
          'funding_attributed_usdt':round(cost,2) if coverage else None,
          'funding_adjusted_closed_trade_pnl_usdt':round(sum(adjusted),2) if coverage else None,
          'funding_adjusted_closed_trade_return_pct':round(sum(adjusted)/100000*100,3) if coverage else None,
          'funding_adjusted_win_rate_pct':round(100*len(wins)/len(adjusted),2) if adjusted and coverage else None,
          'funding_adjusted_pf':round(sum(wins)/-sum(losses),3) if losses and coverage else None,
          'cohorts':{k:{kk:round(vv,2) for kk,vv in v.items()} for k,v in cohorts.items()}
        }
    return result

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--directory',default='research/results')
    p.add_argument('--out',default='research/results/PRB_funding_audit.json')
    a=p.parse_args()
    reports={}
    for path in sorted(Path(a.directory).glob('PRB_*.json')):
        if path.name.endswith('funding_audit.json'):continue
        res=audit(path)
        reports[res['symbol']]=res
        print('FUNDING',res['symbol'],res['funding_rows'],
              'coverage',res['funding_coverage_verified'],
              'max_gap_h',res['funding_max_gap_hours'],flush=True)
        for name,z in res['scenarios'].items():
            print('SCENARIO',name,'closed',z['closed_trades'],'censored',z['censored_open_positions'],
                  'ex_funding',z['realized_return_ex_funding_pct'],
                  'adj_funding',z['funding_adjusted_closed_trade_return_pct'],flush=True)
    if not reports:raise RuntimeError('No PRB input reports')
    Path(a.out).write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf8')

if __name__=='__main__':main()
