"""Bounded <$20 BTC maker classification, TEST wallet only; not a profit test.

Every write is journaled first. Ambiguous writes are matched by exact intent,
never resubmitted. Owned exposure is canceled/reconciled/flattened before exit.
"""
import argparse
from decimal import Decimal
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sentinel.api import ApiError, AmbiguousWrite, Roostoo, decimal_string
from sentinel.state import Store
from sentinel.runner import InstanceLock


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--seconds',type=int,default=300)
    a=ap.parse_args()
    if not 30<=a.seconds<=600:raise ValueError('Bounded test only')
    api=Roostoo(spacing=2.2)
    lock=InstanceLock(Path('runtime')/'sigma_probe.lock')
    store=Store('runtime/sigma_probe.sqlite3')
    if store.get('started'):raise RuntimeError('Probe already ran; preserve its journal, do not repeat to manufacture activity')
    now=api.sync_time()
    accounts=api.balance_accounts()
    wallet=accounts['spot']
    pending=api.query_orders(pending_only=True)
    shorts=api.short_positions()
    cash=wallet['USD']['Free']
    if not 49900<=cash<=50100 or wallet['USD']['Lock'] or pending or shorts or any(sum(v.values()) for v in accounts['margin'].values()) or any(sum(v.values()) for c,v in wallet.items() if c!='USD'):
        raise ApiError('Probe requires the supplied clean near-$50,000 TEST wallet; competition/active accounts refused')
    meta=api.exchange_info()['TradePairs']['BTC/USD']
    quote=api.ticker()['Data']['BTC/USD']
    qty=decimal_string(19/float(quote['MinAsk']),int(meta['AmountPrecision']))
    price=decimal_string(quote['MaxBid'],int(meta['PricePrecision']))
    if float(qty)*float(price)<=float(meta['MiniOrder']):raise ApiError('Probe below exchange minimum')
    store.set('started',True)
    payload={'pair':'BTC/USD','side':'BUY','quantity':qty,'price':price,'request_timestamp':api.now_ms(),'mode':'paper-exchange','purpose':'execution_classification'}
    key=store.intent('single-btc-maker-classification',payload,payload['request_timestamp'])
    store.event('probe_initial',{'usd':cash,'payload':payload,'max_notional':20})
    try:
        r=api.place_limit('BTC/USD','BUY',qty,price)
        store.update_intent(key,'acknowledged',r)
        oid=r['OrderDetail']['OrderID']
    except AmbiguousWrite:
        store.update_intent(key,'unknown')
        oid=None
    deadline=time.monotonic()+a.seconds
    detail=None
    while True:
        try:
            if oid is None:
                candidates=api.query_orders(limit=100)
                candidates=[r for r in candidates if r.get('Pair')=='BTC/USD' and r.get('Side')=='BUY' and r.get('Type')=='LIMIT' and abs(int(r.get('CreateTimestamp',0))-payload['request_timestamp'])<60000 and Decimal(str(r.get('Quantity',0)))==Decimal(qty) and Decimal(str(r.get('Price',0)))==Decimal(price)]
                if len(candidates)!=1:raise ApiError('Ambiguous intent remains unresolved; no resend')
                oid=candidates[0]['OrderID']
                store.update_intent(key,'acknowledged',{'OrderDetail':candidates[0]})
            rows=api.query_orders(order_id=oid)
            if len(rows)!=1:raise ApiError('Owned order missing; no resend')
            detail=rows[0]
            store.event('probe_owned_order',{'order':detail})
            if detail['Status'] in {'FILLED','CANCELED','CANCELLED','REJECTED'}:break
            if time.monotonic()>=deadline:
                try:api.cancel_order(oid)
                except (AmbiguousWrite,ApiError):pass
        except ApiError as e:
            store.event('probe_read_pause',{'error':str(e)})
        time.sleep(15)
    store.update_intent(key,'done',{'OrderDetail':detail})
    wallet=api.balance()
    holding=wallet.get('BTC',{'Free':0,'Lock':0})
    if holding['Lock']:raise ApiError('Terminal buy still locks coin inventory; preserve journal')
    sell_qty=decimal_string(min(float(qty),holding['Free']),int(meta['AmountPrecision']))
    if float(sell_qty)>0:
        payload2={'pair':'BTC/USD','side':'SELL','quantity':sell_qty,'request_timestamp':api.now_ms(),'mode':'paper-exchange','purpose':'probe_cleanup'}
        k2=store.intent('single-btc-probe-cleanup',payload2,payload2['request_timestamp'])
        try:
            r=api.place('BTC/USD','SELL',sell_qty)
            store.update_intent(k2,'acknowledged',r)
        except AmbiguousWrite:
            store.update_intent(k2,'unknown')
            raise ApiError('Cleanup status unknown; preserve journal and reconcile before any other bot') from None
        rows=api.query_orders(order_id=r['OrderDetail']['OrderID'])
        if len(rows)!=1 or rows[0]['Status']!='FILLED':raise ApiError('Cleanup unconfirmed; no duplicate sell')
        store.update_intent(k2,'done',{'OrderDetail':rows[0]})
        store.event('probe_cleanup_fill',{'order':rows[0]})
    end=api.balance_accounts()
    orders=api.query_orders(pending_only=True)
    shorts=api.short_positions()
    if orders or shorts or any(sum(v.values()) for c,v in end['spot'].items() if c!='USD'):raise ApiError('Probe did not return to clean cash state')
    report={'purpose':'authenticated_execution_classification_not_strategy_performance','buy_order':detail,'starting_usd':cash,'ending_usd':end['spot']['USD']['Free'],'cash_change':round(end['spot']['USD']['Free']-cash,2),'pending_orders':len(orders),'shorts':len(shorts),'inventory_zero':True}
    Path('research/sigma_gate/passive_execution_test.json').write_text(json.dumps(report,indent=2))
    store.event('probe_complete',report)
    store.close();lock.close()


if __name__=='__main__':main()
