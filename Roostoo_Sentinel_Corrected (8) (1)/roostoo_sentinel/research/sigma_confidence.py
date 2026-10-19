"""Paired moving-block confidence estimates; descriptive, not a forecast."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

root=Path('research/sigma_gate')
paths={k:root/'comparisons'/k/'test_2026/all_taker/nav.csv' for k in ('sigma-reference-corrected','combined-conservative')}
nav={k:pd.read_csv(p,index_col=0,parse_dates=True).iloc[:,0] for k,p in paths.items()}
returns=pd.DataFrame({k:x.pct_change().dropna() for k,x in nav.items()}).dropna()
n=len(returns);rng=np.random.default_rng(20261010)
result={'source':'historical_simulation','method':'1000 paired moving-block resamples of frozen 2026 hourly net-return series; wrapping at sample end','selection_trials':9,'not_independent_of_upstream_research':True,'results':{}}
for block in (48,168):
    starts=rng.integers(0,n,size=(1000,int(np.ceil(n/block))))
    indices=(starts[:,:,None]+np.arange(block)[None,None,:]).reshape(1000,-1)[:,:n]%n
    sharpe={}
    row={}
    for k in returns:
        sampled=returns[k].to_numpy()[indices]
        sh=sampled.mean(axis=1)/sampled.std(axis=1,ddof=1)*np.sqrt(8760)
        sharpe[k]=sh
        row[k]={'sharpe_ci90':np.quantile(sh,[.05,.95]).tolist(),'bootstrap_fraction_sharpe_positive':float(np.mean(sh>0))}
    delta=sharpe['combined-conservative']-sharpe['sigma-reference-corrected']
    row['paired_sharpe_improvement_ci90']=np.quantile(delta,[.05,.95]).tolist()
    result['results'][str(block)]=row
(root/'confidence.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
