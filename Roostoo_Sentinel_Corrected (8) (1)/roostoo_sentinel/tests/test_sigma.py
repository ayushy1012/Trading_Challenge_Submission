import unittest
from dataclasses import replace

import numpy as np
import pandas as pd

from sentinel.data import HOUR
from sentinel.sigma import PAIRS,SigmaConfig,apply_caps,prepare,target_weights
from sentinel.sigma_backtest import run


def bars(n=1000):
    rng=np.random.default_rng(81)
    out={}
    for j,p in enumerate(PAIRS):
        close=(100+j*10)*np.exp(np.cumsum(rng.normal(.0002,.002,n)))
        out[p]=pd.DataFrame({'timestamp':np.arange(n,dtype=np.int64)*HOUR+1640995200000,'open':close,'high':close*1.003,'low':close*.997,'close':close,'volume':np.full(n,1e5),'quote_volume':np.full(n,2e7)})
    return out


class SigmaTests(unittest.TestCase):
    def test_future_candles_do_not_change_features(self):
        b=bars(1050);cfg=SigmaConfig()
        short=prepare({p:d.iloc[:1000] for p,d in b.items()},cfg)
        full=prepare(b,cfg)
        np.testing.assert_allclose(short.score,full.score[:1000],equal_nan=True)
        np.testing.assert_allclose(short.covariance,full.covariance[:1000],equal_nan=True)
        np.testing.assert_allclose(short.slow_vol,full.slow_vol[:1000],equal_nan=True)

    def test_availability_after_close(self):
        b=bars();f=prepare(b,SigmaConfig())
        self.assertEqual(f.index[0].value//1_000_000,int(b[PAIRS[0]].timestamp.iloc[0])+HOUR)

    def test_misaligned_assets_rejected(self):
        b=bars();b[PAIRS[-1]]=b[PAIRS[-1]].iloc[1:]
        with self.assertRaises(ValueError):prepare(b,SigmaConfig())

    def test_real_gap_resets_warmup_without_inventing_bar(self):
        b={p:d.drop(index=800).reset_index(drop=True) for p,d in bars(1700).items()}
        f=prepare(b,SigmaConfig())
        self.assertEqual(len(f.index),1699)
        self.assertFalse(f.eligible[800:1520].any())

    def test_post_band_caps(self):
        cfg=SigmaConfig()
        w=apply_caps([.5,.5,.3,.3,.3],cfg)
        self.assertLessEqual(np.abs(w).sum(),.95+1e-12)
        self.assertLessEqual(w[0],.4)
        self.assertLessEqual(w[-1],.3)

    def test_weak_gate_is_cash(self):
        cfg=replace(SigmaConfig(),gate_min=2/3)
        f=prepare(bars(),cfg);f.score[900]=1/3
        w,_=target_weights(f,900,np.zeros(5),1e5,{},cfg)
        np.testing.assert_allclose(w,0)

    def test_disabled_shorts_never_open_negative(self):
        cfg=replace(SigmaConfig(),allow_shorts=False)
        f=prepare(bars(),cfg);f.score[900]=-1
        w,_=target_weights(f,900,np.zeros(5),1e5,{},cfg)
        self.assertTrue((w>=0).all())

    def test_side_change_processed_between_sizing_hours(self):
        cfg=replace(SigmaConfig(),sizing_hours=6,sizing_phase=1)
        f=prepare(bars(),cfg);f.score[900]=-1
        w,_=target_weights(f,900,np.full(5,.1),1e5,{},cfg)
        self.assertTrue((w<0).all())

    def test_constant_price_pays_fees(self):
        cfg=SigmaConfig()
        f=prepare(bars(),cfg);f.open[:]=f.close[:]=100;f.high[:]=101;f.low[:]=99;f.score[:]=1
        r=run(f,cfg,f.index[800],f.index[900],'all_taker')
        self.assertLess(r.summary['return'],0)
        self.assertGreater(r.summary['fees_usd'],0)
        self.assertTrue((pd.to_datetime(r.trades.timestamp,utc=True)>=f.index[800]).all())

    def test_short_proceeds_cannot_fund_extra_exposure(self):
        cfg=SigmaConfig()
        f=prepare(bars(),cfg);f.open[:]=f.close[:]=100;f.high[:]=101;f.low[:]=99;f.score[:]=-1
        r=run(f,cfg,f.index[800],f.index[900],'all_taker')
        self.assertGreaterEqual(r.diagnostics.cash_free_proxy.min(),-1e-6)
        self.assertLess(r.summary['return'],0)

    def test_double_cost_is_worse_in_constant_market(self):
        cfg=SigmaConfig();f=prepare(bars(),cfg);f.open[:]=f.close[:]=100;f.high[:]=101;f.low[:]=99;f.score[:]=1
        normal=run(f,cfg,f.index[800],f.index[900],'all_taker')
        stress=run(f,cfg,f.index[800],f.index[900],'double_cost')
        self.assertLess(stress.summary['return'],normal.summary['return'])

    def test_fixed_profit_lock_sticky(self):
        cfg=replace(SigmaConfig(),fixed_long=.9)
        f=prepare(bars(),cfg);s={'initial_nav':1e5}
        w,_=target_weights(f,900,np.zeros(5),100300,s,cfg)
        np.testing.assert_allclose(w,0)
        self.assertEqual(s['fixed_lock'],'goal')
        w,_=target_weights(f,901,np.zeros(5),99900,s,cfg)
        np.testing.assert_allclose(w,0)

    def test_config_rejects_leverage_and_unknown_fields(self):
        with self.assertRaises(ValueError):replace(SigmaConfig(),gross_cap=1.2)
        with self.assertRaises(ValueError):replace(SigmaConfig(),sizing_hours=0)


if __name__=='__main__':unittest.main()
