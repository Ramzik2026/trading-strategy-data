"""Offline integrity tests for historical BingX archive."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import archive_bingx_15m as a

class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patcher=patch.object(a,'ROOT',Path(self.tmp.name))
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
    @staticmethod
    def rows(n):
        return [{'time':i*a.Q,'open':'100','high':'102','low':'99','close':'101','volume':'4'} for i in range(n)]
    def test_append_and_restart_idempotence(self):
        rows=self.rows(8)
        self.assertEqual(a.persist('BTC-USDT',rows[:4]),4)
        self.assertEqual(a.previous_last('BTC-USDT'),3*a.Q)
        self.assertEqual(a.persist('BTC-USDT',rows[3:]),4)
        self.assertEqual(a.persist('BTC-USDT',rows),0)
    def test_modified_historic_row_rejected(self):
        rows=self.rows(2)
        a.persist('ETH-USDT',rows)
        mutated=[dict(rows[0],high='103')]
        with self.assertRaises(ValueError):a.persist('ETH-USDT',mutated)
    def test_interior_gap_rejected(self):
        rows=self.rows(3)
        with self.assertRaises(ValueError):a.persist('SOL-USDT',[rows[0],rows[2]])

if __name__=='__main__':unittest.main()
