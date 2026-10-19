"""Bind a remote ledger to its authenticated account, preserving key rotation.

Never reuse a test account's risk baseline or strategy evidence for competition
funds. Credentials themselves are not written to the ledger.
"""
import hashlib
import json

from .api import ApiError


def bind_account(api,store):
    fingerprint=hashlib.sha256(api.api_key.encode()).hexdigest()
    previous=store.get('roostoo_account_fingerprint')
    if previous==fingerprint:return
    owned=[]
    for payload,response in store.db.execute('SELECT payload,response FROM intents WHERE response IS NOT NULL ORDER BY created_ms DESC'):
        p,r=json.loads(payload),json.loads(response)
        detail=r.get('OrderDetail',{}) if isinstance(r,dict) else {}
        if p.get('mode')=='paper-exchange' and p.get('side') in {'BUY','SELL'} and detail.get('OrderID') is not None:
            owned.append((p,detail))
        if len(owned)>=2:break
    legacy=store.get('tick_counters') is not None or bool(store.unfinished())
    if (previous or legacy) and not owned:
        raise ApiError('Cannot verify legacy account identity. Preserve this ledger; use a separate runtime directory for a different account')
    for payload,expected in owned:
        found=api.query_orders(order_id=expected['OrderID'])
        if len(found)!=1:raise ApiError('Credentials do not own this ledger. Test and competition accounts require separate runtime directories')
        actual=found[0]
        if str(actual.get('OrderID'))!=str(expected['OrderID']) or any(actual.get(field)!=expected.get(field) for field in ('Pair','Side','Type','CreateTimestamp') if field in expected):
            raise ApiError('Account identity conflicts with the recorded owned order')
        if abs(float(actual.get('Quantity',0))-float(payload['quantity']))>1e-8:
            raise ApiError('Account identity conflicts with the recorded order quantity')
    store.set('roostoo_account_fingerprint',fingerprint)
    store.event('tick_account_verified',{'credential_fingerprint':fingerprint,'owned_orders_checked':len(owned),'risk_baseline_preserved':True})
