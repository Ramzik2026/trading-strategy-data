import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from supervisor_gate import evaluate_asset_arm

def fixture():
    return {'window':['2025-01-01','2026-01-02'],
            'funding_included':True,'split_info':'Untouched frozen forward holdout',
            'scenarios':{'baseline':{'segments':{
              'full':{'closed_trades':80,'profit_factor_ex_funding':1.4,
                      'return_ex_funding_pct':5.0,'max_drawdown_pct_15m':7.2,
                      'censored_open_positions':0},
              'holdout_20pct':{'closed_trades':17,'return_ex_funding_pct':1.1}}}}}
class TestGate(unittest.TestCase):
    def test_eligible_only_for_manual_review(self):
        r=evaluate_asset_arm(fixture(),'baseline')
        self.assertTrue(r['approved'])
        self.assertEqual(r['action'],'ELIGIBLE_FOR_MANUAL_REVIEW')
    def test_no_funding_blocks(self):
        x=fixture();x['funding_included']=False
        r=evaluate_asset_arm(x,'baseline')
        self.assertFalse(r['approved'])
        self.assertIn('FUNDING_NOT_MODELLED',r['reasons'])
    def test_short_and_seen_holdout_block(self):
        x=fixture();x['window']=['2026-09-25','2026-11-23']
        x['split_info']='Exploratory (NOT an untouched independent holdout)'
        r=evaluate_asset_arm(x,'baseline')
        self.assertIn('WINDOW_SHORTER_THAN_365D',r['reasons'])
        self.assertIn('HOLDOUT_NOT_UNTOUCHED',r['reasons'])
    def test_negative_pnl_blocks(self):
        x=fixture();x['scenarios']['baseline']['segments']['full']['return_ex_funding_pct']=-5
        r=evaluate_asset_arm(x,'baseline')
        self.assertIn('NONPOSITIVE_FULL_RETURN',r['reasons'])
if __name__=='__main__':unittest.main()
