import builtins
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from sentinel.api import ApiError,Roostoo
from sentinel.runner import InstanceLock


class SchemaTests(unittest.TestCase):
    def test_live_spot_wallet_is_used_and_margin_is_separate(self):
        api=Roostoo(api_key='fixture',secret='fixture')
        with patch.object(api,'_request',return_value={'Success':True,'SpotWallet':{'USD':{'Free':'49999.96','Lock':0}},'MarginWallet':{'USD':{'Free':20,'Lock':1}}}):
            self.assertEqual(api.balance()['USD']['Free'],49999.96)
            self.assertEqual(api.balance_accounts()['margin']['USD']['Lock'],1)

    def test_legacy_wallet_still_works(self):
        api=Roostoo()
        with patch.object(api,'_request',return_value={'Success':True,'Wallet':{'USD':{'Free':50000,'Lock':0}}}):
            self.assertEqual(api.balance()['USD']['Free'],50000)

    def test_missing_or_malformed_balance_is_an_error_not_zero_cash(self):
        api=Roostoo()
        for value in [None,{}, {'BTC':{'Free':1,'Lock':0}}, {'USD':{'Free':'NaN','Lock':0}}, {'USD':{'Free':-1,'Lock':0}}, {'USD':{'Free':True,'Lock':0}}, {'USD':{'Free':1}}]:
            with self.subTest(value=value), patch.object(api,'_request',return_value={'Success':True,'SpotWallet':value}):
                with self.assertRaises(ApiError):api.balance()

    def test_invalid_spot_is_not_hidden_by_valid_legacy_wallet(self):
        api=Roostoo()
        with patch.object(api,'_request',return_value={'SpotWallet':{},'Wallet':{'USD':{'Free':1,'Lock':0}}}):
            with self.assertRaises(ApiError):api.balance()

    def test_api_secret_environment_alias_and_explicit_precedence(self):
        with patch.dict(os.environ,{'ROOSTOO_API_KEY':'fixture-key','ROOSTOO_SECRET_KEY':'','ROOSTOO_API_SECRET':'alias'}):
            self.assertEqual(Roostoo().secret,'alias')
            self.assertEqual(Roostoo(secret='explicit').secret,'explicit')
        with patch.dict(os.environ,{'ROOSTOO_SECRET_KEY':'primary','ROOSTOO_API_SECRET':'alias'}):
            self.assertEqual(Roostoo().secret,'primary')

    def test_first_page_omits_offset_and_following_page_preserves_it(self):
        api=Roostoo()
        with patch.object(api,'_request',return_value={'Success':True,'OrderMatched':[]}) as call:
            api.query_orders(pending_only=True)
            self.assertNotIn('offset',call.call_args.args[2])
            self.assertEqual(call.call_args.args[2]['pending_only'],'TRUE')
            api.query_orders(offset=100)
            self.assertEqual(call.call_args.args[2]['offset'],'100')

    def test_order_id_query_has_no_other_optional_fields(self):
        api=Roostoo()
        with patch.object(api,'_request',return_value={'OrderMatched':[]}) as call:
            api.query_orders(order_id=123,offset=100,pending_only=True)
            self.assertEqual(call.call_args.args[2],{'order_id':'123'})

    def test_missing_order_list_is_unknown_not_an_empty_account(self):
        api=Roostoo()
        for response in [{'Success':True},{'Success':True,'OrderMatched':{}},{'Success':True,'OrderMatched':[None]}]:
            with patch.object(api,'_request',return_value=response):
                with self.assertRaises(ApiError):api.query_orders()

    def test_missing_short_position_list_is_unknown_not_an_empty_account(self):
        api=Roostoo()
        with patch.object(api,'_request',return_value={'Success':True}):
            with self.assertRaises(ApiError):api.short_positions()
        with patch.object(api,'_request',return_value={'Success':True,'Positions':[]}):
            self.assertEqual(api.short_positions(),[])

    def test_pagination_is_validated_before_request(self):
        api=Roostoo()
        for args in [{'offset':-1},{'offset':True},{'limit':0},{'limit':101}]:
            with self.assertRaises(ValueError):api.query_orders(**args)

    def test_windows_contention_is_normalized_and_file_is_closed(self):
        original=builtins.__import__
        def windows_import(name,*args,**kwargs):
            if name=='fcntl':raise ImportError('Windows fixture')
            return original(name,*args,**kwargs)
        fake=types.SimpleNamespace(LK_NBLCK=1,locking=lambda *args:(_ for _ in ()).throw(PermissionError('already locked')))
        with tempfile.TemporaryDirectory() as d, patch.dict(sys.modules,{'msvcrt':fake}), patch('builtins.__import__',side_effect=windows_import):
            with self.assertRaisesRegex(RuntimeError,'Another bot'):InstanceLock(Path(d)/'bot.lock')
            (Path(d)/'bot.lock').unlink()


if __name__=='__main__':unittest.main()
