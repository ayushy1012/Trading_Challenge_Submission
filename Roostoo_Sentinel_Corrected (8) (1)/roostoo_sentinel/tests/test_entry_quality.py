import contextlib
from dataclasses import replace
import io
from pathlib import Path
import tempfile
import unittest

from sentinel.api import ApiError
from sentinel.state import Store
from sentinel.tick import TickConfig,TickEngine
from sentinel.tick_risk import completed_cycles
from test_tick_corrections import META,NOW,Venue,quotes


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'test.sqlite3');self.api=Venue()
        self.cfg=replace(TickConfig(),runtime_dir=self.temp.name,entry_reference='last_pullback',regime_filter='quiet',require_quote_movement=True)
        self.e=TickEngine(self.cfg,self.api,self.store,META,'paper-exchange')
        self.output=contextlib.redirect_stdout(io.StringIO());self.output.__enter__()

    def tearDown(self):
        self.output.__exit__(None,None,None);self.store.close();self.temp.cleanup()

    def warm(self,moving=True,volatile=False):
        rows=[]
        for i in range(60,0,-1):
            last=3.89e-6 if not volatile or i<30 else 3.7e-6
            bid=3.87e-6 if moving and i%2 else 3.88e-6
            rows.append({'t':NOW-i*30000,'quotes':{'PEPE/USD':last},'executable':{'PEPE/USD':[bid,bid+1e-8]}})
        self.store.set('tick_quote_history',rows)

    def test_buy_requires_a_pullback_when_last_is_at_bid(self):
        self.assertEqual(self.e.entry_price(quotes(3.88e-6,3.89e-6,3.88e-6)['Data']['PEPE/USD'],8),3.87e-6)

    def test_quiet_policy_requires_a_real_thirty_minute_history(self):
        self.e.step(quotes(),NOW)
        decision=self.store.get('tick_last_decision')
        self.assertEqual(decision['required_history_seconds'],1800)
        self.assertEqual(self.api.writes,[])

    def test_last_price_flicker_with_fixed_bid_and_ask_cannot_trigger_entry(self):
        self.warm(moving=False);self.e.step(quotes(),NOW)
        self.assertEqual(self.api.writes,[])
        self.assertEqual(self.store.get('tick_last_decision')['pair_checks'][0]['reason'],'no_executable_quote_movement')

    def test_moving_quiet_quotes_allow_only_a_small_probe(self):
        self.warm();self.e.step(quotes(),NOW)
        self.assertEqual(len(self.api.orders),1)
        row=self.api.orders[0];self.assertLessEqual(row['Price']*row['Quantity'],100)

    def test_volatile_market_is_rejected_even_when_current_price_has_recovered(self):
        self.warm(volatile=True);self.e.step(quotes(),NOW)
        self.assertEqual(self.api.writes,[])
        self.assertEqual(self.store.get('tick_last_decision')['pair_checks'][0]['reason'],'market_not_quiet')

    def test_margin_funds_are_not_silently_ignored(self):
        self.e.quotes=quotes();self.api.last_balance_accounts={'margin':{'USD':{'Free':1,'Lock':0}}}
        with self.assertRaises(ApiError):self.e.ownership(self.api.wallet,self.e.state())

    def test_changed_watchlist_warms_new_quotes_without_reusing_missing_pairs(self):
        self.store.set('tick_quote_history',[{'t':NOW-1800000,'quotes':{'OLD/USD':1}}])
        self.e.step(quotes(),NOW)
        self.assertEqual(self.store.get('tick_last_decision')['reason'],'warming_quote_history')
        self.assertEqual(len(self.store.get('tick_quote_history')),1)

    def test_account_cent_uncertainty_cannot_establish_a_tiny_profitable_cycle(self):
        self.e.quotes=quotes();pos={'cost':100.,'qty':100/3.88e-6,'entry':3.88e-6,'cycle_id':1,'config_hash':self.cfg.fingerprint}
        sample,_=self.e.sale_sample(pos,'PEPE/USD',pos['qty'],0,.02,NOW,{'id':2})
        row=completed_cycles([sample],'paper-exchange',self.cfg.fingerprint)[0]
        self.assertGreater(row['cycle_pnl'],0)
        self.assertLess(row['validation_cycle_return'],0)


if __name__=='__main__':unittest.main()
