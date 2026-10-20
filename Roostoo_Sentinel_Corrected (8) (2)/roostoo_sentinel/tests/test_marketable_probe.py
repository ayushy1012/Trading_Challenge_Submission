import contextlib
from dataclasses import replace
import io
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'research'))
from probe_marketable_limits import MarketableProbe
from sentinel.state import Store
from sentinel.tick import TickConfig,TickEngine
from test_tick_corrections import META,NOW,Venue,quotes


class ProbeTests(unittest.TestCase):
    def test_marketable_price_and_exact_base_fee_quanta(self):
        with tempfile.TemporaryDirectory() as d,contextlib.redirect_stdout(io.StringIO()):
            store=Store(Path(d)/'p.sqlite3');venue=Venue();e=TickEngine(replace(TickConfig(),runtime_dir=d),venue,store,META,'paper-exchange')
            p=MarketableProbe(e,NOW+300000)
            p.step(quotes(),NOW)
            buy=venue.orders[0]
            self.assertEqual(buy['Price'],quotes()['Data']['PEPE/USD']['MinAsk'])
            self.assertEqual(buy['Quantity']%2000,0)
            self.assertLessEqual(buy['Quantity']*buy['Price'],20)
            venue.fill(buy);venue.now=NOW+30000;p.step(quotes(),NOW+30000)
            self.assertEqual(venue.orders[-1]['Side'],'SELL')
            self.assertEqual(venue.orders[-1]['Price'],quotes()['Data']['PEPE/USD']['MaxBid'])
            store.close()

    def test_expired_probe_does_not_place_a_buy(self):
        with tempfile.TemporaryDirectory() as d,contextlib.redirect_stdout(io.StringIO()):
            store=Store(Path(d)/'p.sqlite3');venue=Venue();e=TickEngine(replace(TickConfig(),runtime_dir=d),venue,store,META,'paper-exchange')
            self.assertTrue(MarketableProbe(e,NOW).step(quotes(),NOW))
            self.assertEqual(venue.writes,[]);store.close()


if __name__=='__main__':unittest.main()
