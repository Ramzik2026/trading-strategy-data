"""Preflight regression checks. All fixtures synthetic, no API/network access."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from year_backtest import Bar, HOUR, FOUR
from prb_v14 import Q, aggregate, detect_pairs, fee_risk,exit_bar,frac_levels

class PRBTests(unittest.TestCase):
    def test_aggregate_1h(self):
        a=[Bar(i*Q,100+i,102+i,99+i,101+i,3) for i in range(4)]
        b=aggregate(a,4)
        self.assertEqual(len(b),1)
        self.assertEqual((b[0].o,b[0].h,b[0].l,b[0].c,b[0].v),(100,105,99,104,12))
    def test_aggregate_gap_rejected(self):
        a=[Bar(i*Q,100,102,99,101,3) for i in (0,1,3,4)]
        with self.assertRaises(ValueError):aggregate(a,4)
    def test_confirmation_one_right_bar(self):
        four=[
            Bar(0,100,110,90,102,1),
            Bar(FOUR,100,108,80,100,1),
            Bar(2*FOUR,100,115,95,107,1),
        ]
        q=[Bar(i*Q,100,115,95,100,1) for i in range(48)]
        q[16]=Bar(FOUR,100,108,80,100,1)
        # Level formed after 3rd 4h closure; no future needed for availability
        z=frac_levels(four,q,.1)
        lows=[x for x in z if x['side']==1 and x['price']==80]
        self.assertEqual(len(lows),1)
        self.assertEqual(lows[0]['confirmed'],3*FOUR)
    def test_long_pair_with_real_rejection(self):
        base=4*FOUR
        c1=Bar(base,102,106,99,100,2)
        c2=Bar(base+HOUR,100,108,101,107,2)
        levels=[
            dict(side=1,price=100.0,center_t=0,confirmed=FOUR,expiry=100*FOUR,swept=None,id='L'),
            dict(side=-1,price=118.0,center_t=0,confirmed=FOUR,expiry=100*FOUR,swept=None,id='H'),
        ]
        # Need at least one wick below L with body above L: C1 is red close=100 and low=99
        # but midpoint pair's open/close meet L=100 exactly.
        out,st=detect_pairs([c1,c2],levels,.1,base,base+3*HOUR)
        self.assertEqual(len(out),1)
        self.assertEqual(out[0]['lo'],99)
        self.assertEqual(out[0]['hi'],100)
        self.assertEqual(out[0]['tp'],117.9)
    def test_gap_rejection(self):
        base=4*FOUR
        c1=Bar(base,102,106,99,100,2)
        c2=Bar(base+HOUR,101,108,100,107,2)
        out,st=detect_pairs([c1,c2],[],.1,base,base+3*HOUR)
        self.assertEqual(len(out),0)
        self.assertEqual(st['RB_GAP'],1)
    def test_ambiguous_bar_stop_first(self):
        z={'side':1,'stop':95,'tp':115}
        p={'z':z,'bars':1}
        px,why=exit_bar(p,Bar(0,100,116,94,102,1),0)
        self.assertEqual((px,why),(95,'SL_FIRST_AMBIGUOUS'))
    def test_stop_gap(self):
        p={'z':{'side':1,'stop':95,'tp':115},'bars':1}
        px,why=exit_bar(p,Bar(0,90,100,89,99,1),0)
        self.assertEqual((px,why),(90,'STOP_GAP'))
    def test_rr_after_cost(self):
        self.assertIsNone(fee_risk(1,100,99,101,.0006,.0002,.001))
        self.assertIsNotNone(fee_risk(1,100,95,115,.0006,.0002,.001))
    def test_retest_cannot_use_formation_hour(self):
        # Covers confirmation time and the pairing boundaries independent of
        # any later 15m trigger. The replay must only activate at c2 close.
        base=4*FOUR
        c1=Bar(base,102,106,99,100,2)
        c2=Bar(base+HOUR,100,108,101,107,2)
        levels=[
            dict(side=1,price=100.0,center_t=0,confirmed=FOUR,expiry=100*FOUR,swept=None,id='L'),
            dict(side=-1,price=118.0,center_t=0,confirmed=FOUR,expiry=100*FOUR,swept=None,id='H')]
        out,_=detect_pairs([c1,c2],levels,.1,base,base+3*HOUR)
        self.assertEqual(out[0]['ready'],base+2*HOUR)

if __name__=='__main__':unittest.main()
