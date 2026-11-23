#!/usr/bin/env python3
"""Deterministic governance supervisor for research results, NOT an AI trading signal.

Policy is intentionally strict and may be changed via versioned review.
Independent per-asset approval; success on all assets is NOT mandatory.
No real trades, trading keys, or private endpoints.
"""
import argparse
import datetime as dt
import json
from pathlib import Path

MIN_DAYS=365
MIN_CLOSED_TRADES=60
MIN_PROFIT_FACTOR=1.15
MAX_DRAWDOWN_PCT=12.0

def evaluate_asset_arm(report,arm):
    reasons=[]
    a=report['scenarios'][arm]
    full=a['segments']['full']
    hold=a['segments']['holdout_20pct']
    start,end=[dt.date.fromisoformat(x) for x in report['window']]
    days=(end-start).days
    if days<MIN_DAYS:reasons.append('WINDOW_SHORTER_THAN_365D')
    if full['closed_trades']<MIN_CLOSED_TRADES:reasons.append('INSUFFICIENT_CLOSED_TRADES')
    if not report['funding_included']:reasons.append('FUNDING_NOT_MODELLED')
    if 'NOT an untouched' in report.get('split_info',''):reasons.append('HOLDOUT_NOT_UNTOUCHED')
    pf=full.get('profit_factor_ex_funding')
    if pf is None or pf<MIN_PROFIT_FACTOR:reasons.append('PROFIT_FACTOR_UNPROVEN')
    if full['return_ex_funding_pct']<=0:reasons.append('NONPOSITIVE_FULL_RETURN')
    dd=full.get('max_drawdown_pct_15m')
    if dd is None or dd>MAX_DRAWDOWN_PCT:reasons.append('DRAWDOWN_LIMIT_OR_MISSING')
    if hold.get('closed_trades',0)<15:reasons.append('HOLDOUT_SAMPLE_TOO_SMALL')
    if hold['return_ex_funding_pct']<=0:reasons.append('HOLDOUT_NOT_POSITIVE')
    if full.get('censored_open_positions',0)>0:reasons.append('OPEN_POSITION_CENSORED')
    return {'approved':not reasons,'action':'RESEARCH_ONLY' if reasons else 'ELIGIBLE_FOR_MANUAL_REVIEW',
            'reasons':reasons,'full_trade_count':full['closed_trades'],
            'full_return_ex_funding_pct':full['return_ex_funding_pct'],
            'holdout_return_ex_funding_pct':hold['return_ex_funding_pct'],
            'full_profit_factor_ex_funding':pf}

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--reports',default='research/results')
    p.add_argument('--output',default='research/results/PRB_governance.json')
    args=p.parse_args()
    reports=list(sorted(Path(args.reports).glob('PRB_variants_*.json')))
    if len(reports)!=3:raise RuntimeError('Expect exactly 3 independently run asset reports')
    all_results={}
    for path in reports:
        obj=json.loads(path.read_text(encoding='utf8'))
        asset=obj['symbol']
        all_results[asset]={k:evaluate_asset_arm(obj,k) for k in obj['scenarios']}
    eligible=[f'{asset}/{arm}' for asset,arms in all_results.items()
              for arm,x in arms.items() if x['approved']]
    out={'policy_version':'TDA Supervisor governance v0.1',
         'policy_thresholds':{'min_calendar_days':MIN_DAYS,'min_closed_trades':MIN_CLOSED_TRADES,
                              'profit_factor_min':MIN_PROFIT_FACTOR,
                              'max_drawdown_pct':MAX_DRAWDOWN_PCT},
         'trade_execution_enabled':False,
         'approved_models':eligible,'tested_asset_strategy_pairs':sum(map(len,all_results.values())),
         'status':'RESEARCH_ONLY' if not eligible else 'MANUAL_REVIEW_REQUIRED',
         'per_asset':all_results}
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf8')
    print('GOVERNANCE status',out['status'],'model_asset_tests',out['tested_asset_strategy_pairs'],
          'approved',len(eligible),flush=True)
    for asset,arms in all_results.items():
        counts={}
        for v in arms.values():
            for reason in v['reasons']:counts[reason]=counts.get(reason,0)+1
        print('ASSET',asset,'tested',len(arms),'rejection_counts',json.dumps(counts),flush=True)

if __name__=='__main__':main()
