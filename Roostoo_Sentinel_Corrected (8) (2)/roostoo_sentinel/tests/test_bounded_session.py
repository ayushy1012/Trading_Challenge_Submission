import contextlib
from dataclasses import replace
import io
from pathlib import Path
import tempfile
import unittest

from sentinel.state import Store
from sentinel.tick import TickConfig,TickEngine,session_stop_reason,resume_completed_session
from test_tick_corrections import META,NOW,Venue,quotes


class BoundedTests(unittest.TestCase):
    def test_resume_completed_session_preserves_profit_and_loss_latches(self):
        with tempfile.TemporaryDirectory() as d,contextlib.redirect_stdout(io.StringIO()):
            store=Store(Path(d)/'probe.sqlite3')
            store.set('tick_session_stop',{'reason':'bounded_session_time_limit'})
            store.set('tick_bounded_session',{'end_ms':NOW})
            store.set('tick_counters',{'total_profit_halted':True,'initial_equity':50000})
            resume_completed_session(store,NOW)
            self.assertIsNone(store.get('tick_session_stop'))
            self.assertIsNone(store.get('tick_bounded_session'))
            self.assertTrue(store.get('tick_counters')['total_profit_halted'])
            store.close()

    def test_resume_never_erases_unknown_write_or_owned_position(self):
        with tempfile.TemporaryDirectory() as d,contextlib.redirect_stdout(io.StringIO()):
            store=Store(Path(d)/'probe.sqlite3');stop={'reason':'bounded_session_time_limit'}
            store.set('tick_session_stop',stop);store.set('tick_state',{'position':{'qty':1}})
            resume_completed_session(store,NOW);self.assertEqual(store.get('tick_session_stop'),stop)
            store.set('tick_state',{});store.intent('unknown',{'side':'BUY'},NOW)
            resume_completed_session(store,NOW);self.assertEqual(store.get('tick_session_stop'),stop)
            store.close()

    def test_entry_budget_waits_for_inventory_and_unresolved_writes(self):
        session={'end_ms':NOW+1000000,'max_entries':1}
        for state,unresolved in [({'order':{'id':1}},False),({'position':{'qty':1}},False),({},True)]:
            self.assertIsNone(session_stop_reason(session,NOW,1,state,unresolved))
        self.assertEqual(session_stop_reason(session,NOW,1,{}),'bounded_session_entry_limit')

    def test_time_budget_initiates_cleanup_even_with_unknown_write(self):
        session={'end_ms':NOW,'max_entries':1}
        self.assertEqual(session_stop_reason(session,NOW,0,{'order':{'id':1}},True),'bounded_session_time_limit')

    def test_session_stop_blocks_entries_and_cancels_only_owned_entry(self):
        with tempfile.TemporaryDirectory() as d,contextlib.redirect_stdout(io.StringIO()):
            store=Store(Path(d)/'probe.sqlite3');api=Venue();cfg=replace(TickConfig(),runtime_dir=d)
            engine=TickEngine(cfg,api,store,META,'paper-exchange');engine.quotes=quotes()
            store.set('tick_session_stop',{'reason':'bounded_session_time_limit'})
            engine.step(quotes(),NOW)
            self.assertEqual(api.writes,[])
            self.assertEqual(store.get('tick_last_decision')['reason'],'bounded_session_time_limit')
            state=engine.state();state['pair']='PEPE/USD';engine.submit(state,'BUY',100/3.88e-6,3.88e-6,NOW)
            engine.step(quotes(),NOW+30000)
            self.assertEqual(api.writes,[('LIMIT','BUY'),('CANCEL',api.orders[0]['OrderID'])])
            store.close()


if __name__=='__main__':unittest.main()
