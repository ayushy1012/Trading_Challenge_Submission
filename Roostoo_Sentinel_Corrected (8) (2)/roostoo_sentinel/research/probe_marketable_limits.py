"""Bounded TEST-account execution experiment, not a profitability strategy.

Places at most one <=$20 PEPE LIMIT buy at ask and one LIMIT sell at bid.
Measures whether a marketable LIMIT is a maker or a taker on this mock venue.
The intentional round trip normally loses spread plus fees. On deadline it
cancels, reconciles and market-exits ONLY its own remaining quantity. Resume the
same directory after an interruption; never discard an unresolved journal.
"""
from dataclasses import replace
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sentinel.api import ApiError,Roostoo
from sentinel.execution import validated_quote,wallet_qty
from sentinel.runner import InstanceLock
from sentinel.state import Store
from sentinel.tick import TickConfig,TickEngine
from sentinel.tick_report import export_report


class MarketableProbe:
    def __init__(self,engine,deadline,notional=20.):
        if not 10.1<=notional<=20:raise ValueError('Probe notional must be $10.10..$20')
        self.engine,self.store,self.deadline,self.notional=engine,engine.store,deadline,notional

    def step(self,quotes,now):
        e=self.engine;e.quotes=quotes;s=e.state()
        e.resolve_writes(s)
        if self.store.unfinished():return False
        e.observe(s,now);s=e.state();wallet=e.wallet();e.ownership(wallet,s)
        if s.get('order'):
            if now>=self.deadline:e.cancel(s,now,'test_probe_deadline')
            return False
        if s.get('position'):
            bid,ask=validated_quote(quotes,'PEPE/USD')
            qty=min(s['position']['qty'],wallet['PEPE']['Free'])
            e.submit(s,'SELL',qty,bid,now,'MARKET' if now>=self.deadline else 'LIMIT')
            return False
        if self.store.get('marketable_probe_attempted') or now>=self.deadline:return True
        bid,ask=validated_quote(quotes,'PEPE/USD')
        # Base-fee quanta for 0.05% and 0.10% are both integral for this size.
        # This avoids creating unsellable base dust in the classification test.
        qty=int(self.notional/ask)//2000*2000
        if qty*ask<=e.c.minimum_notional:raise ApiError('Insufficient precision-compatible probe size')
        s['pair']='PEPE/USD';e.save(s)
        self.store.set('marketable_probe_attempted',True)
        self.store.event('marketable_probe_intent',{'pair':'PEPE/USD','notional_ceiling':self.notional,'purpose':'Measure LIMIT role/fee; this spread-paying round trip is not expected to profit.'},now)
        e.submit(s,'BUY',qty,ask,now)
        return False


def run(directory='runtime/marketable_test_probe'):
    api=Roostoo(spacing=2.)
    lock=InstanceLock(Path(directory)/'bot.lock');store=Store(Path(directory)/'tick_paper_exchange.sqlite3')
    try:
        now=api.sync_time();meta=api.exchange_info()
        cfg=replace(TickConfig(),runtime_dir=directory,probe_notional=20.)
        engine=TickEngine(cfg,api,store,meta,'paper-exchange')
        saved=store.get('marketable_probe_deadline')
        if saved is None:
            accounts=api.balance_accounts();quotes=api.ticker()
            usd=accounts['spot']['USD']
            # The credentials supplied for this investigation identify a near
            # $50k test wallet. Refuse a $100k competition account or handover.
            if not 49000<=usd['Free']+usd['Lock']<=51000 or usd['Lock']>0:
                raise ApiError('This experiment requires the supplied near-$50k clean TEST account')
            if any(v['Free']+v['Lock']>0 for coin,v in accounts['spot'].items() if coin!='USD') or any(v['Free']+v['Lock']>0 for v in accounts['margin'].values()) or api.short_positions() or api.query_orders(pending_only=True):
                raise ApiError('Test wallet is not clean; no orders will be touched')
            saved=api.now_ms()+300000;store.set('marketable_probe_deadline',saved)
            store.set('marketable_probe_start_wallet',accounts['spot'])
        probe=MarketableProbe(engine,saved)
        while True:
            start=time.monotonic()
            try:
                quotes=api.ticker()
                if abs(api.now_ms()-int(quotes.get('ServerTime',api.now_ms())))>60000:raise ApiError('Stale quotes')
                done=probe.step(quotes,api.now_ms())
                if done:
                    accounts=api.balance_accounts();pending=api.query_orders(pending_only=True)
                    original=store.get('marketable_probe_start_wallet')['USD']
                    end=accounts['spot']['USD']
                    report={'purpose':'TEST LIMIT role classification; not a strategy-profit sample','cleanup_confirmed':not pending and not store.unfinished() and not engine.state().get('position'),'start_usd':original,'end_usd':end,'observed_cash_change':end['Free']+end['Lock']-original['Free']-original['Lock'],'filled_orders':store.fills(),'pending_orders':pending,'final_wallet':accounts['spot']}
                    if not report['cleanup_confirmed']:raise ApiError('Cleanup not confirmed; preserve test journal')
                    store.set('marketable_probe_result',report)
                    export_report(store.db,'paper-exchange',Path(directory)/'reports',api.now_ms())
                    print(json.dumps(report,indent=2,allow_nan=False));break
            except ApiError as error:store.event('test_probe_pause',{'error':str(error)},api.now_ms())
            time.sleep(max(0,30-(time.monotonic()-start)))
    finally:store.close();lock.close()


if __name__=='__main__':run()
