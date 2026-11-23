import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from year_backtest import Bar,HOUR
from independent_agents_v1 import detect,rsi_wilder,replay

class IndependentAgentTests(unittest.TestCase):
    def test_rsi_rising_to_100(self):
        self.assertEqual(rsi_wilder([float(i) for i in range(31)])[-1],100)
    def test_rsi_falling_to_0(self):
        self.assertEqual(rsi_wilder([float(31-i) for i in range(31)])[-1],0)
    def test_prefix_invariance(self):
        import math
        rows=[]
        for i in range(270):
            o=200+20*math.sin(i*.2)
            c=o+2*math.cos(i*.5)
            rows.append(Bar(i*HOUR,o,max(o,c)+3,min(o,c)-3,c,100+(i%10)*30))
        prior=detect(rows)
        for i in range(270,295):
            rows.append(Bar(i*HOUR,150,155,145,149,180))
        newer=detect(rows)
        for name in prior:
            self.assertEqual(prior[name],{i:v for i,v in newer[name].items() if i<270})
    def test_next_open_conservative_stop(self):
        rows=[Bar(0,100,101,99,100,1),
              Bar(HOUR,100,118,94,110,2),
              Bar(2*HOUR,110,111,105,106,2)]
        result=replay(rows,{0:(1,95,115)},HOUR,3*HOUR,0,0)
        self.assertEqual(result['closed_trades'],1)
        self.assertEqual(result['trades'][0]['reason'],'STOP_AMBIGUOUS')
        self.assertEqual(result['pnl_ex_funding_usdt'],-500)
if __name__=='__main__':unittest.main()
