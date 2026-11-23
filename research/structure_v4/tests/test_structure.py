import unittest
from datetime import datetime,timezone
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from structure import (Candle,Gap,HOUR_MS,FOUR_MS,parse_rows,verify_consistency,
                       weekly_filter,known_pivots,bos_at,find_candidate,
                       forced_stop_at_touch,run_trade)

def candle(hour,o=100,h=101,l=99,c=100):
    return Candle(hour*HOUR_MS,o,h,l,c)

class StructureTests(unittest.TestCase):
    def test_missing_hourly_rejected(self):
        rows=[{'time':c.t,'open':c.o,'high':c.h,'low':c.l,'close':c.c} for c in [candle(x) for x in range(12) if x!=4]]
        with self.assertRaisesRegex(ValueError,'Missing candles'):
            parse_rows(rows,0,12*HOUR_MS,HOUR_MS)

    def test_1h_4h_consistency(self):
        c=[candle(x) for x in range(8)]
        agg=[Candle(0,100,101,99,100),Candle(FOUR_MS,100,101,99,100)]
        verify_consistency(c,agg)
        with self.assertRaisesRegex(ValueError,'mismatch'):
            verify_consistency(c,[Candle(0,100,115,99,100),agg[1]])

    def test_weekly_no_current_week(self):
        candles=[]
        start=int(datetime(2026,1,5,tzinfo=timezone.utc).timestamp()*1000)
        for i in range(42*10):
            k=i//42
            price=100+3*k
            candles.append(Candle(start+i*FOUR_MS,price,price+1,price-1,price))
        trend=weekly_filter(candles)
        self.assertEqual(trend[candles[-1].t],1)
        self.assertEqual(trend[candles[7*42+3].t],0)
        alternative=list(candles)
        for j in range(9*42,len(alternative)):
            old=alternative[j]
            alternative[j]=Candle(old.t,1,2,.5,1)
        self.assertEqual(trend[candles[9*42+3].t],weekly_filter(alternative)[candles[9*42+3].t])

    def test_bos_requires_confirmed_fractal_and_cross(self):
        series=[candle(i,100,101,99,100) for i in range(20)]
        series[4]=candle(4,100,115,99,100)
        series[10]=candle(10,100,116,99,116)
        pivots=known_pivots(series)
        self.assertEqual(pivots[0][10],(4,115))
        self.assertTrue(bos_at(series,10,Gap(2,1,99,100,1),pivots))
        self.assertFalse(bos_at(series,5,Gap(2,1,99,100,1),pivots))

    def test_touch_and_stop_same_hour_counted_loss(self):
        g=Gap(2,1,100,105,5)
        h4=[Candle(i*FOUR_MS,100,115,90,105) for i in range(10)]
        h1=[candle(i,105,107,105,106) for i in range(60)]
        h1[12]=candle(12,105,108,98,106)
        entry,bos,instant=find_candidate(h1,h4,g,known_pivots(h1))
        self.assertIsNone(entry)
        self.assertIsNone(bos)
        self.assertEqual(instant,12)
        self.assertLess(forced_stop_at_touch(g,h1,instant)['net_r'],-1)

    def test_next_open_fills_and_stop_first(self):
        g=Gap(2,1,100,105,5)
        h1=[candle(i,106,120,95,110) for i in range(55)]
        result=run_trade(h1,0,g)
        self.assertEqual(result['reason'],'stop')
        self.assertLess(result['net_r'],-1)

if __name__=='__main__': unittest.main()
