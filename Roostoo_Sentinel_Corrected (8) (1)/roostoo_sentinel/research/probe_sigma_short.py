"""One autonomous $20 TEST-only short accounting check; immediately closes.

No retry of a mutation. The durable journal remains available if reconciliation
cannot establish ownership. This measures permissions/fees, not trading edge.
"""
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sentinel.api import ApiError,AmbiguousWrite,Roostoo
from sentinel.runner import InstanceLock
from sentinel.state import Store


def main():
    api=Roostoo(spacing=2.2);api.sync_time()
    lock=InstanceLock(Path('runtime')/'sigma_probe.lock')
    store=Store('runtime/sigma_short_probe.sqlite3')
    accounts=api.balance_accounts();wallet=accounts['spot']
    resumed=bool(store.get('started'))
    if not resumed and (not 49900<=wallet['USD']['Free']<=50100 or wallet['USD']['Lock'] or api.short_positions() or api.query_orders(pending_only=True) or any(sum(v.values()) for c,v in wallet.items() if c!='USD') or any(sum(v.values()) for v in accounts['margin'].values())):
        raise ApiError('Requires clean supplied near-$50,000 TEST account')
    start=store.get('initial_usd',wallet['USD']['Free']);now=api.now_ms()
    if resumed:
        row=store.db.execute("SELECT id,payload,response FROM intents ORDER BY created_ms LIMIT 1").fetchone()
        if not row:raise ApiError('Started test lacks its intent; no mutation')
        key,payload,opened=row[0],json.loads(row[1]),json.loads(row[2]) if row[2] else {}
        now=payload['request_timestamp']
    else:
        store.set('initial_usd',start);store.set('started',True)
        payload={'pair':'BTC/USD','collateral':'20.00','mode':'paper-exchange','side':'SHORT_OPEN','request_timestamp':now}
        key=store.intent('one-bounded-short-permission-test',payload,now)
    report={'purpose':'execution_classification_not_strategy_performance','starting_usd':start,'account_source':'supplied near-$50,000 TEST wallet'}
    try:
        if not resumed:
            opened=api.short_open('BTC/USD','20.00')
            store.update_intent(key,'acknowledged',opened)
    except ApiError as e:
        store.update_intent(key,'rejected',{'error':str(e)})
        report.update(supported=False,error=str(e),mutations_accepted=0)
        Path('research/sigma_gate/short_execution_test.json').write_text(json.dumps(report,indent=2))
        store.event('short_probe_rejected',report);store.close();lock.close();return
    except AmbiguousWrite:
        store.update_intent(key,'unknown');opened={}
    positions=api.short_positions()
    matches=[s for s in positions if s['Pair']=='BTC/USD' and abs(int(s['CreateTimestamp'])-now)<60000 and (str(s['ID'])==str(opened.get('ID')) if opened else 0<float(s['Collateral'])<=20)]
    if len(matches)!=1:raise ApiError('Short intent ambiguous; no resend or unverified close')
    position=matches[0]
    store.update_intent(key,'done',opened or position)
    live_accounts=api.balance_accounts();quotes=api.ticker()
    report.update(supported=True,open_response=opened,position=position,accounts_while_open=live_accounts,quote_while_open=quotes['Data']['BTC/USD'])
    store.event('short_probe_open_verified',report)
    k2=store.intent('one-bounded-short-cleanup',{'pair':'BTC/USD','side':'SHORT_CLOSE','position_id':position['ID']},api.now_ms())
    if k2 is None:raise ApiError('Cleanup intent already exists; reconcile it without a repeated close')
    try:
        closed=api.short_close('BTC/USD')
        store.update_intent(k2,'acknowledged',closed)
    except AmbiguousWrite:
        store.update_intent(k2,'unknown')
        if api.short_positions():raise ApiError('Close status unknown; preserve journal, no resend') from None
        closed={'reconciled_closed_position':True}
    remaining=api.short_positions();pending=api.query_orders(pending_only=True);end=api.balance_accounts()
    if remaining or pending or end['spot']['USD']['Lock'] or any(sum(v.values()) for v in end['margin'].values()):raise ApiError('Cleanup not verified')
    store.update_intent(k2,'done',closed)
    report.update(close_response=closed,ending_usd=end['spot']['USD']['Free'],cash_change=round(end['spot']['USD']['Free']-start,2),remaining_positions=0,pending_orders=0)
    Path('research/sigma_gate/short_execution_test.json').write_text(json.dumps(report,indent=2))
    store.event('short_probe_complete',report);store.close();lock.close()


if __name__=='__main__':main()
