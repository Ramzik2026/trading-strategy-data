import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from annual import Bar, Gap, BAR_MS, detect, normalize, evaluate_future, simulate, study

def sample(n=390):
    import random
    rng=random.Random(1122)
    p=100.0
    out=[]
    for i in range(n):
        op=p
        close=op*(1+rng.gauss(0,.012))
        h=max(op,close)*(1+rng.random()*.008)
        l=min(op,close)*(1-rng.random()*.008)
        out.append(Bar(i*BAR_MS,op,h,l,close))
        p=close
    return out

class AnnualResearchTests(unittest.TestCase):
    def test_missing_bar_raises(self):
        c=sample(32)
        rows=[{'time':b.t,'open':b.o,'high':b.h,'low':b.l,'close':b.c} for b in c]
        rows.pop(10)
        with self.assertRaisesRegex(ValueError,'Incomplete'):
            normalize(rows,0,32*BAR_MS)

    def test_entry_candle_ambiguous_counts_stop(self):
        g=Gap(2,1,100,105,5)
        f=[Bar(i*BAR_MS,105,115,98,108) for i in range(25)]
        self.assertEqual(evaluate_future(f,g)['outcome'],'stop')

    def test_simulation_never_looks_forward(self):
        c=sample()
        g=next(x for x in detect(c) if 90<x.i<250)
        a=simulate(c,g,draws=120,seed=19)
        mutated=list(c)
        for i in range(g.i+1,len(c)):
            b=c[i]
            mutated[i]=Bar(b.t,b.o,b.h*1.2,b.l*.8,b.c)
        self.assertEqual(a,simulate(mutated,g,draws=120,seed=19))

    def test_repeatable_walkforward(self):
        c=sample()
        a=study(c,draws=120,warmup=90)
        self.assertEqual(a,study(c,draws=120,warmup=90))
        for period in ('development','holdout','all'):
            x=a['periods'][period]
            self.assertLessEqual(x['filtered']['n'],x['baseline']['n'])
