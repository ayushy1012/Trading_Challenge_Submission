import contextlib
import hashlib
import io
from pathlib import Path
import tempfile
import unittest

from sentinel.account import bind_account
from sentinel.api import ApiError
from sentinel.state import Store


class Account:
    api_key='fixture-new-key'
    def __init__(self,rows=None):self.rows=rows or [];self.queries=[]
    def query_orders(self,order_id):self.queries.append(order_id);return self.rows


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'state.sqlite3')
        self.stdout=contextlib.redirect_stdout(io.StringIO());self.stdout.__enter__()

    def tearDown(self):self.stdout.__exit__(None,None,None);self.store.close();self.temp.cleanup()

    def legacy(self):
        self.store.set('tick_counters',{'initial_equity':50000.,'total_profit_halted':True})
        order={'OrderID':123,'Pair':'PEPE/USD','Side':'BUY','Type':'LIMIT','Quantity':2000,'CreateTimestamp':12345}
        key=self.store.intent('owned',{'mode':'paper-exchange','side':'BUY','quantity':'2000'},12345)
        self.store.update_intent(key,'done',{'OrderDetail':order})
        return order

    def test_fresh_account_binds_without_storing_the_key(self):
        api=Account();bind_account(api,self.store)
        self.assertEqual(self.store.get('roostoo_account_fingerprint'),hashlib.sha256(api.api_key.encode()).hexdigest())
        self.assertNotIn(api.api_key,' '.join(x[0] for x in self.store.db.execute('SELECT value FROM kv')))
        self.assertEqual(api.queries,[])

    def test_a_new_credential_cannot_reuse_a_different_accounts_baseline(self):
        self.legacy();api=Account()
        with self.assertRaises(ApiError):bind_account(api,self.store)
        self.assertIsNone(self.store.get('roostoo_account_fingerprint'))
        self.assertEqual(self.store.get('tick_counters')['initial_equity'],50000)

    def test_verified_same_account_key_rotation_preserves_risk_latch(self):
        order=self.legacy();self.store.set('roostoo_account_fingerprint','old-fingerprint')
        api=Account([order]);bind_account(api,self.store)
        self.assertTrue(self.store.get('tick_counters')['total_profit_halted'])
        self.assertEqual(api.queries,[123])

    def test_matching_id_with_conflicting_order_is_rejected(self):
        order=self.legacy();api=Account([{**order,'CreateTimestamp':99}])
        with self.assertRaises(ApiError):bind_account(api,self.store)

    def test_wallet_only_legacy_state_cannot_establish_account_identity(self):
        self.store.set('tick_counters',{'initial_equity':50000})
        with self.assertRaises(ApiError):bind_account(Account(),self.store)


if __name__=='__main__':unittest.main()
