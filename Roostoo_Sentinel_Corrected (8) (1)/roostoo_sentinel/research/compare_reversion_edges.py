"""Exploratory fair-price discounts, tested against all three fill models.

No claim of a pristine holdout: the source history was already researched.
All entry masks use only completed current and previous minute candles.
"""
from dataclasses import asdict
import json
from pathlib import Path
import numpy as np
import pandas as pd
from evaluate_tick_cycles import load,one_coin,Spec

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'research/paper_edge_comparison'


def main():
    a=load('PEPE');tick=1e-8
    close=pd.Series(a['close']);low=pd.Series(a['low']).rolling(30).min();high=pd.Series(a['high']).rolling(30).max()
    quiet=(np.abs(a['ret30'])<=.004)&(a['range30']<=8*tick)
    masks={
        'mean_discount':quiet&(a['close']<=a['ema60']-tick)&(a['ema60']>=pd.Series(a['ema60']).shift(30).to_numpy()-tick),
        'floor_bounce':quiet&(a['close']<=low.to_numpy()+2*tick)&(a['close']>=low.to_numpy()+tick)&(a['close']>close.shift(5).to_numpy())&(a['ema60']>=pd.Series(a['ema60']).shift(30).to_numpy()-tick),
        'stationary_floor':quiet&(a['close']<=low.to_numpy()+.25*(high-low).to_numpy())&(np.abs(close.rolling(60).median()-close.rolling(60).median().shift(30)).to_numpy()<=tick),
    }
    registration={'filters':list(masks),'entry_offsets':[1,2],'target_ticks':[1,2],'fill_models':['touch','sampled','through'],'stop_ticks':3,'ttl_minutes':15,'hold_minutes':30,'caveat':'Exploratory additional tests on reused history; no untouched holdout, no Roostoo fills.'}
    (DEST/'reversion_registration.json').write_text(json.dumps(registration,indent=2))
    start,end=(int(pd.Timestamp(x,tz='UTC').timestamp()*1000) for x in ['2026-09-01','2026-10-09'])
    result=[]
    for name,mask in masks.items():
        source={**a,'warm':a['warm']&np.nan_to_num(mask,nan=False)}
        for entry in [1,2]:
            for target in [1,2]:
                models={}
                for fill in ['touch','sampled','through']:
                    spec=Spec(name,entry_offset=entry,target_ticks=target,filter='precomputed',fill=fill,ttl_minutes=15)
                    r,tr=one_coin(source,'PEPE',tick,spec,start,end,True);models[fill]=r
                    pd.DataFrame(tr).to_csv(DEST/f'reversion_{name}_e{entry}_t{target}_{fill}.csv',index=False)
                row={'filter':name,'entry_offset':entry,'target_ticks':target,'models':models,'minimum_return':min(r['return'] for r in models.values())}
                result.append(row)
                print(json.dumps({'filter':name,'entry':entry,'target':target,'returns_pct':{k:round(v['return']*100,4) for k,v in models.items()},'cycles':{k:v['trades'] for k,v in models.items()}}),flush=True)
    result.sort(key=lambda x:x['minimum_return'],reverse=True)
    (DEST/'reversion_results.json').write_text(json.dumps({'registration':registration,'ranked':result},indent=2,allow_nan=False))


if __name__=='__main__':main()
