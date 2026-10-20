"""Reproduce pinned unmodified v1.1 components on independently downloaded data."""
import json
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sentinel.data import read_bars
from sentinel.sigma import PAIRS
from sentinel.sigma_origin.strategy.core import StrategyParams,compute
from sentinel.sigma_origin.portfolio.policy import PolicyParams
from sentinel.sigma_origin.backtest.engine import ExecParams,run
from sentinel.sigma_origin.metrics import summary


if __name__=='__main__':
    bars={p:read_bars('data/sigma',p) for p in PAIRS}
    panels={k:pd.DataFrame({p:d[k].to_numpy() for p,d in bars.items()},index=pd.to_datetime(bars[PAIRS[0]].timestamp,unit='ms',utc=True)) for k in ['close','high','low','quote_volume']}
    sp=StrategyParams(sleeves=('tgate',),prior_weights={'tgate':1},universe=PAIRS,min_daily_usd=1e6,gate_lookbacks=(168,336,720),gate_dead_band=.5)
    pol=PolicyParams(mode='long_short',sizing='absolute',short_regime_max=2,short_scale=.5,max_weight=.3,max_weight_major=.4,band_abs=.03,band_rel=.2)
    out=compute(panels['close'],panels['quote_volume'],sp,{p:2 if p!='XRP/USD' else 4 for p in PAIRS})
    # Frozen reference bounds from the upstream checked-in NAV, not rolling now.
    start=pd.Timestamp('2025-05-25T10:00:00Z')
    end=pd.Timestamp('2026-10-02T09:00:00Z')
    ticks=pd.Series({p:.01 if p!='XRP/USD' else .0001 for p in PAIRS})
    hs=(ticks/panels['close'].median()*5000).clip(lower=.2)
    r=run(out,panels['close'],panels['high'],panels['low'],pol,ExecParams(),start=start,end=end,half_spread_bps=hs)
    s=summary(r.nav,fees_paid=r.fees,turnover=r.turnover,n_trades=len(r.trades))
    s.update({'source':'independent_reference_simulation','start':str(start),'end_exclusive':str(end),'same_close_market_fills':True,'terminal_liquidation_fee_charged':False,'published_return':.20870052806691097,'maker_share':r.maker_ratio,'half_spread_proxy_bps':hs.to_dict()})
    root=Path('research/sigma_gate/origin_reproduction');root.mkdir(parents=True,exist_ok=True)
    r.nav.to_csv(root/'nav.csv');r.trades.to_csv(root/'trade_logs.csv',index=False)
    (root/'summary.json').write_text(json.dumps(s,indent=2))
    print(json.dumps(s,indent=2),flush=True)
