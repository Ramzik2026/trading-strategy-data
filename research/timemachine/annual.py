#!/usr/bin/env python3
"""Independent read-only BingX FVG TimeMachine research. No private APIs or orders.

The gate is specified before seeing outcomes: MC touch rate >= .20 and
conditional net-R > 0. Both outcomes and Monte Carlo are evaluated only on
closed 4H candles; the MC history at formation cannot include future candles.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import random
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BAR_MS = 14_400_000
COST_BPS = 14  # hypothetical total costs, NOT BingX published fees
MIN_ZONE_PCT = 0.1
WAIT_BARS = 12
HOLD_BARS = 12
STOP_BUFFER = 0.0005
RR = 2.0
BASE = 'https://open-api.bingx.com/openApi/swap/v3/quote/klines'


@dataclass(frozen=True)
class Bar:
    t: int
    o: float
    h: float
    l: float
    c: float


@dataclass(frozen=True)
class Gap:
    i: int
    side: int
    lo: float
    hi: float
    width: float


def normalize(rows, start, end):
    found = {}
    for x in rows:
        t = int(x['time'])
        if not start <= t < end or t % BAR_MS:
            continue
        b = Bar(t, *(float(x[k]) for k in ('open', 'high', 'low', 'close')))
        if not all(math.isfinite(v) and v > 0 for v in (b.o, b.h, b.l, b.c)):
            raise ValueError('Invalid price or non-finite bar')
        if not b.l <= min(b.o, b.c) <= max(b.o, b.c) <= b.h:
            raise ValueError(f'Invalid OHLC at {t}')
        if t in found and found[t] != b:
            raise ValueError(f'Conflicting candle at {t}')
        found[t] = b
    ordered = [found[k] for k in sorted(found)]
    expected = (end - start) // BAR_MS
    if len(ordered) != expected or any(b.t != start+i*BAR_MS for i, b in enumerate(ordered)):
        raise ValueError(f'Incomplete 4H history: {len(ordered)}/{expected} bars')
    return ordered


def read_page(symbol, start, end):
    params = urlencode({'symbol':symbol,'interval':'4h','startTime':start,
                        'endTime':end-1,'limit':400})
    error = None
    for attempt in range(4):
        try:
            request = Request(BASE + '?' + params, headers={'User-Agent':'BingX-FVG-Research/3.0','Accept':'application/json'})
            with urlopen(request, timeout=25) as resp:
                result = json.load(resp)
            if not isinstance(result, dict) or str(result.get('code', 0)) != '0' or not isinstance(result.get('data'), list):
                raise ValueError('Unexpected BingX API payload')
            return result['data']
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            error = f'{type(exc).__name__}: {exc}'[:160]
            if attempt < 3:
                time.sleep(2 ** attempt + 1)
    raise RuntimeError(f'{symbol} public candle request failed: {error}')


def fetch_year(symbol, days):
    if not symbol.endswith('-USDT') or not symbol.replace('-', '').isalnum():
        raise ValueError('Unsupported pair identifier')
    end = int(time.time()*1000)//BAR_MS * BAR_MS  # exclude incomplete bar
    start = end-days*6*BAR_MS
    rows = []
    for offset in range(0, days*6, 390):
        a = start + offset*BAR_MS
        b = min(a + 390*BAR_MS, end)
        rows.extend(read_page(symbol, a, b))
        time.sleep(1.10)  # documented BingX 1 request/s per IP
    return normalize(rows, start, end), end


def detect(candles):
    out = []
    for i in range(2, len(candles)):
        a, z = candles[i-2], candles[i]
        side = 1 if z.l > a.h else -1 if z.h < a.l else 0
        if not side:
            continue
        lo, hi = (a.h, z.l) if side == 1 else (z.h, a.l)
        w = 100*(hi-lo)/lo
        if w > MIN_ZONE_PCT:
            out.append(Gap(i, side, lo, hi, w))
    return out


def order(g):
    entry = g.hi if g.side == 1 else g.lo
    stop = g.lo*(1-STOP_BUFFER) if g.side == 1 else g.hi*(1+STOP_BUFFER)
    risk = abs(entry-stop)
    target = entry + g.side*RR*risk
    return entry,stop,target,risk


def overlaps(b, g):
    return b.l <= g.hi and b.h >= g.lo


def evaluate_future(future, g, *, cost_bps=COST_BPS):
    entry,stop,target,risk = order(g)
    filled_at = next((i for i,b in enumerate(future[:WAIT_BARS]) if overlaps(b,g)), None)
    if filled_at is None:
        return None
    tail = future[filled_at:filled_at+HOLD_BARS+1]
    if len(tail) < HOLD_BARS+1:
        return None
    gross, outcome = 0.0, 'time'
    for j,b in enumerate(tail):
        stop_hit = b.l <= stop if g.side == 1 else b.h >= stop
        target_hit = b.h >= target if g.side == 1 else b.l <= target
        if stop_hit:
            gross, outcome = -1.0, 'stop'
            break
        # Do not award TP on entry candle when OHLC order is ambiguous.
        if j > 0 and target_hit:
            gross, outcome = RR, 'target'
            break
    else:
        gross = g.side*(tail[-1].c-entry)/risk
    return {'outcome':outcome,'gross_r':gross,'net_r':gross-(cost_bps/10000)*entry/risk}


def simulate(c, g, *, draws, seed, block=4, cost_bps=COST_BPS):
    if g.i < 60 or draws < 20 or block < 1:
        raise ValueError('Insufficient history or invalid simulation params')
    # Strict cutoff: historical completed bars before the FVG formation candle.
    past = c[:g.i]
    ratios = [(past[j].o/past[j-1].c,past[j].h/past[j-1].c,
               past[j].l/past[j-1].c,past[j].c/past[j-1].c)
              for j in range(1,len(past))]
    rng = random.Random(seed)
    touches = 0
    net_results = []
    for _ in range(draws):
        p = c[g.i].c
        synth = []
        while len(synth) < WAIT_BARS+HOLD_BARS:
            k = rng.randrange(len(ratios)-block+1)
            for o,h,l,close in ratios[k:k+block]:
                synth.append(Bar(0,p*o,p*h,p*l,p*close))
                p *= close
                if len(synth) >= WAIT_BARS+HOLD_BARS:
                    break
        # The last touch at index 11 needs 13 bars incl. entry, hence 24 total.
        ans = evaluate_future(synth,g,cost_bps=cost_bps)
        if ans is not None:
            touches += 1
            net_results.append(ans['net_r'])
    pt = touches/draws
    avg = statistics.mean(net_results) if net_results else None
    return {'p_touch':pt,'expected_net_r_conditional':avg,
            'allow':pt>=.20 and avg is not None and avg>0}


def stats(rows):
    if not rows:
        return {'n':0,'mean_net_r':None,'mean_gross_r':None,'wins':0,'tp':0,'sl':0,'time':0}
    return {'n':len(rows),'mean_net_r':round(statistics.mean(x['net_r'] for x in rows),5),
            'mean_gross_r':round(statistics.mean(x['gross_r'] for x in rows),5),
            'wins':sum(x['net_r']>0 for x in rows),'tp':sum(x['outcome']=='target' for x in rows),
            'sl':sum(x['outcome']=='stop' for x in rows),'time':sum(x['outcome']=='time' for x in rows)}


def study(c, *, draws=1000, warmup=270, cost_bps=COST_BPS, seed=19):
    split = int(len(c)*.60)
    signals = []
    for g in detect(c):
        if g.i < warmup or g.i+WAIT_BARS+HOLD_BARS >= len(c):
            continue
        actual = evaluate_future(c[g.i+1:g.i+1+WAIT_BARS+HOLD_BARS],g,cost_bps=cost_bps)
        model = simulate(c,g,draws=draws,seed=seed+g.i*97,cost_bps=cost_bps)
        if actual is None:
            continue
        # Pre-signal market regime: 7-day return, available AT formation.
        pre = c[g.i-42].c
        change = (c[g.i].c/pre)-1
        regime = 'up' if change >= .05 else 'down' if change <= -.05 else 'sideways'
        signals.append({'formed_utc':dt.datetime.fromtimestamp((c[g.i].t+BAR_MS)/1000,dt.timezone.utc).isoformat(),
                        'bar':g.i,'period':'holdout' if g.i>=split else 'development',
                        'regime':regime,'side':'long' if g.side==1 else 'short',
                        'width_pct':round(g.width,4),'gate':model['allow'],
                        'model_p_touch':round(model['p_touch'],5),
                        'model_net_r_conditional':model['expected_net_r_conditional'],**actual})
    report={}
    for group in ('development','holdout','all'):
        subset=[s for s in signals if group=='all' or s['period']==group]
        a=stats(subset)
        b=stats([s for s in subset if s['gate']])
        report[group]={'baseline':a,'filtered':b,'delta_mean_net_r':None if a['mean_net_r'] is None or b['mean_net_r'] is None else round(b['mean_net_r']-a['mean_net_r'],5)}
    regimes={x:{'baseline':stats([s for s in signals if s['period']=='holdout' and s['regime']==x]),
                'filtered':stats([s for s in signals if s['period']=='holdout' and s['regime']==x and s['gate']])}
             for x in ('up','sideways','down')}
    return {'periods':report,'holdout_regimes':regimes,'signals':signals,
            'parameters':{'draws':draws,'warmup_bars':warmup,'cost_bps_hypothetical':cost_bps,
                          'min_p_touch':.20,'min_conditional_net_r':0,'block_bars':4,
                          'split_index':split,'no_real_trades':True},
            'caveats':['No portfolio accounting: candidate positions may overlap.',
                        'No tick ordering, funding, liquidation, actual fees, spread or slippage.',
                        'Bootstrap touch probability is not a calibrated forecast.',
                        'Holdout is chronological, but sample regimes and overlapping trades cause dependence.']}


def make_report(results, path):
    lines=['# BingX FVG TimeMachine — annual read-only experiment','',
           'Numbers are simulated historical signals, NOT trading-account returns.',
           'Thresholds: FVG >0.1%; first retest <=12 bars; stop buffer 0.05%; TP 2R; hold <=12 bars.',
           'Monte Carlo gate: >=20% synthetic touch, expected net-R given touch >0.',
           'Fees: 14 bps *hypothetical* total notional cost; no funding or live order fills.',
           '', '| Contract | Holdout baseline n | Mean net R | Filter n | Mean net R | Difference |',
           '|---|---:|---:|---:|---:|---:|']
    for sym,r in results.items():
        h=r['study']['periods']['holdout']
        a,b=h['baseline'],h['filtered']
        fmt=lambda v:'—' if v is None else f'{v:+.3f}'
        lines.append(f'| {sym} | {a["n"]} | {fmt(a["mean_net_r"])} | {b["n"]} | {fmt(b["mean_net_r"])} | {fmt(h["delta_mean_net_r"])} |')
    lines.extend(['','## Interpretation','',
                  '- Improvement of mean R on selected trades alone does not show higher account profits.',
                  '- Model selection must not be tuned using the holdout results.',
                  '- Do not enable live trading based on this report.'])
    path.write_text('\n'.join(lines)+'\n',encoding='utf-8')


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--symbols',nargs='+',default=['BTC-USDT','ETH-USDT','SOL-USDT'])
    ap.add_argument('--days',type=int,default=365)
    ap.add_argument('--draws',type=int,default=1000)
    ap.add_argument('--out',type=Path,default=Path('research-output'))
    args=ap.parse_args()
    if not 45 <= args.days <= 730 or args.draws < 100:
        ap.error('days must be 45..730 and draws >=100')
    args.out.mkdir(parents=True,exist_ok=True)
    results={}
    for sym in args.symbols:
        print('Fetching read-only 4H data for',sym,flush=True)
        candles,end=fetch_year(sym,args.days)
        raw={'source':'BingX public USDT perpetual 4H candles','symbol':sym,
             'asof_utc':dt.datetime.fromtimestamp(end/1000,dt.timezone.utc).isoformat(),
             'candles':[dict(time=x.t,open=x.o,high=x.h,low=x.l,close=x.c) for x in candles]}
        (args.out/f'{sym}_raw.json').write_text(json.dumps(raw,indent=1)+'\n')
        r=study(candles,draws=args.draws)
        results[sym]={'asof_utc':raw['asof_utc'],'bars':len(candles),'study':r}
        (args.out/f'{sym}_result.json').write_text(json.dumps(results[sym],indent=2)+'\n')
        h=r['periods']['holdout']
        print(sym,'holdout:',h,flush=True)
    (args.out/'summary.json').write_text(json.dumps(results,indent=2)+'\n')
    make_report(results,args.out/'REPORT.md')
    print((args.out/'REPORT.md').read_text(),flush=True)


if __name__=='__main__':
    main()
