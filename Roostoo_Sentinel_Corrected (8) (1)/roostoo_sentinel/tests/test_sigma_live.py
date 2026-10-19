import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock

import numpy as np

from sentinel.api import ApiError,Roostoo
from sentinel.sigma import PAIRS,SigmaConfig
from sentinel.sigma_live import account_snapshot,normalize_order,risk_reason,SigmaRunner
from sentinel.state import Store


def ticker():return {'Data':{p:{'MaxBid':100,'MinAsk':100} for p in PAIRS}}


class SigmaLiveTests(unittest.TestCase):
    def test_pending_echo_is_not_fill(self):
        o={'Quantity':2,'FilledQuantity':2,'FilledAverPrice':0,'CommissionChargeValue':0,'CoinChange':0,'UnitChange':0,'Status':'PENDING'}
        self.assertEqual(normalize_order(o,0,0)['FilledQuantity'],0)
        with self.assertRaises(ApiError):normalize_order(o,0,1)

    def test_actual_partial_fill_preserved(self):
        o={'Quantity':2,'FilledQuantity':1,'FilledAverPrice':100,'CommissionChargeValue':.05,'CoinChange':1,'UnitChange':-100.05}
        self.assertEqual(normalize_order(o,0,1)['FilledQuantity'],1)

    def test_invalid_overfill_rejected(self):
        o={'Quantity':2,'FilledQuantity':3,'FilledAverPrice':100,'CommissionChargeValue':.05}
        with self.assertRaises(ApiError):normalize_order(o,0,3)

    def test_collateral_not_double_counted(self):
        a={'spot':{'USD':{'Free':80,'Lock':20}},'margin':{}}
        s=[{'Pair':PAIRS[0],'Collateral':20,'ShortQty':.2,'EntryPrice':100}]
        t=ticker();t['Data'][PAIRS[0]]['MinAsk']=105
        nav,free,qty,values=account_snapshot(a,s,t,SigmaConfig())
        self.assertAlmostEqual(nav,98.979)
        self.assertEqual(qty[0],-.2)

    def test_missing_collateral_lock_rejected(self):
        a={'spot':{'USD':{'Free':100,'Lock':0}},'margin':{}}
        with self.assertRaises(ApiError):account_snapshot(a,[{'Pair':PAIRS[0],'Collateral':20,'ShortQty':.2,'EntryPrice':100}],ticker(),SigmaConfig())

    def test_funded_margin_wallet_not_assumed_spot(self):
        a={'spot':{'USD':{'Free':100,'Lock':0}},'margin':{'USD':{'Free':1,'Lock':0}}}
        with self.assertRaises(ApiError):account_snapshot(a,[],ticker(),SigmaConfig())

    def test_foreign_holdings_rejected(self):
        a={'spot':{'USD':{'Free':100,'Lock':0},'PEPE':{'Free':1,'Lock':0}},'margin':{}}
        with self.assertRaises(ApiError):account_snapshot(a,[],ticker(),SigmaConfig())

    def test_permanent_halt_survives_day_change(self):
        cfg=SigmaConfig();r={}
        risk_reason(100,1791590400000,r,cfg)
        self.assertEqual(risk_reason(97,1791590500000,r,cfg),'total_loss')
        self.assertEqual(risk_reason(100,1791763300000,r,cfg),'total_loss')

    def test_profit_stop_is_persistent(self):
        cfg=SigmaConfig();r={}
        risk_reason(100,1791590400000,r,cfg)
        self.assertEqual(risk_reason(101.2,1791590500000,r,cfg),'total_profit')
        self.assertEqual(risk_reason(100,1791763300000,r,cfg),'total_profit')

    def test_three_losses_block(self):
        cfg=SigmaConfig();r={}
        risk_reason(100,1791590400000,r,cfg);r['consecutive_losses']=3
        self.assertEqual(risk_reason(100,1791590500000,r,cfg),'consecutive_losses')

    def test_zero_fill_cancellation_does_not_reset_losses(self):
        with tempfile.TemporaryDirectory() as td:
            runner=SigmaRunner.__new__(SigmaRunner)
            runner.cfg=SigmaConfig();runner.store=Store(Path(td)/'test.sqlite3')
            runner.meta={p:{'AmountPrecision':5} for p in PAIRS};runner.owned=[0.]*5
            runner.risk={'consecutive_losses':2};runner.api=Roostoo()
            key=runner.store.intent('x',{'pair':PAIRS[0],'before_qty':0},100)
            runner._finish({'id':key,'payload':{'pair':PAIRS[0],'before_qty':0}},{'quantity':0,'order':{'FilledAverPrice':0}},0)
            self.assertEqual(runner.risk['consecutive_losses'],2)
            self.assertEqual(runner.store.fills(),[])
            runner.store.close()

    def test_short_close_quantity_is_reduce_only_parameter(self):
        class Capture(Roostoo):
            def _request(self,*args,**kwargs):return args,kwargs
        api=Capture();args,kwargs=api.short_close(PAIRS[0],'0.01')
        self.assertEqual(args[2],{'pair':PAIRS[0],'close_qty':'0.01'})
        self.assertTrue(kwargs['mutation'])
        with self.assertRaises(ValueError):api.short_close(PAIRS[0],'-1')

    def test_expired_session_cannot_journal_or_send_an_entry(self):
        runner=SigmaRunner.__new__(SigmaRunner)
        runner.session={'deadline_ms':100,'stopping':False}
        runner.stop_requested=False
        runner.api=Mock();runner.api.now_ms.return_value=101
        runner.store=Mock()
        runner.submit(PAIRS[0],'SHORT_OPEN',1,None,'allocation',{},np.zeros(5),100,100)
        self.assertTrue(runner.session['stopping'])
        runner.store.intent.assert_not_called()
        runner.api.short_open.assert_not_called()

    def test_short_merge_uses_exchange_weighted_entry_once(self):
        with tempfile.TemporaryDirectory() as td:
            runner=SigmaRunner.__new__(SigmaRunner)
            runner.cfg=SigmaConfig();runner.store=Store(Path(td)/'test.sqlite3')
            runner.meta={p:{'AmountPrecision':5} for p in PAIRS};runner.owned=[-1.,0,0,0,0]
            runner.risk={};runner.api=Roostoo()
            runner.store.set('sigma_cost_basis',{PAIRS[0]:{'basis':100.,'realized':-.1}})
            payload={'pair':PAIRS[0],'side':'SHORT_OPEN','before_qty':-1}
            key=runner.store.intent('merge',payload,100)
            runner._finish({'id':key,'payload':payload},{'quantity':1,'response':{'EntryPrice':150,'OpenFee':.2}},-2)
            book=runner.store.get('sigma_cost_basis',{})[PAIRS[0]]
            self.assertEqual(book['basis'],150)
            self.assertAlmostEqual(book['realized'],-.3)
            runner.store.close()

    def test_short_close_uses_capped_exchange_realized_loss(self):
        with tempfile.TemporaryDirectory() as td:
            runner=SigmaRunner.__new__(SigmaRunner)
            runner.cfg=SigmaConfig();runner.store=Store(Path(td)/'test.sqlite3')
            runner.meta={p:{'AmountPrecision':5} for p in PAIRS};runner.owned=[-1.,0,0,0,0]
            runner.risk={};runner.api=Roostoo()
            runner.store.set('sigma_cost_basis',{PAIRS[0]:{'basis':100.,'realized':-.1}})
            payload={'pair':PAIRS[0],'side':'SHORT_CLOSE','before_qty':-1}
            key=runner.store.intent('close',payload,100)
            fill={'quantity':1,'response':{'ClosePrice':250,'RealizedPNL':-100,'CloseFee':.25}}
            runner._finish({'id':key,'payload':payload},fill,0)
            self.assertAlmostEqual(fill['closed_or_dust_position_net_pnl'],-100.35)
            self.assertEqual(runner.risk['consecutive_losses'],1)
            runner.store.close()


if __name__=='__main__':unittest.main()
