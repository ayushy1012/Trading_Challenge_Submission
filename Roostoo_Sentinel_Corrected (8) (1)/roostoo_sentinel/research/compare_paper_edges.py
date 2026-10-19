"""Predeclared tick-policy comparison with alternative execution assumptions.

These are development comparisons on already inspected Binance USDT candles,
not actual Roostoo trades or an untouched historical holdout. The new Oct 9
one-day check is loaded only after the comparison grid is written to disk.
"""
from dataclasses import asdict,replace
import hashlib
import io
import json
from pathlib import Path
import sys
import zipfile

import numpy as np
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sentinel.data import RAW_COLUMNS,get_bytes
from evaluate_tick_cycles import Spec,load,one_coin,MINUTE

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'research/paper_edge_comparison'
COINS=['PEPE','SHIB','BONK']
SPECS=[
    Spec('join_last',entry_offset=0,ttl_minutes=10),
    Spec('pullback_one',ttl_minutes=10),
    Spec('quiet_pullback_one',filter='quiet',ttl_minutes=10),
    Spec('quiet_pullback_two',entry_offset=2,filter='quiet',ttl_minutes=10),
    Spec('quiet_one_target_two',target_ticks=2,filter='quiet',ttl_minutes=10),
    Spec('quiet_two_target_two',entry_offset=2,target_ticks=2,filter='quiet',ttl_minutes=10),
    Spec('quiet_one_stop_two',stop_ticks=2,filter='quiet',ttl_minutes=10),
    Spec('quiet_two_stop_two',entry_offset=2,stop_ticks=2,filter='quiet',ttl_minutes=10),
    Spec('quiet_one_hold_60',filter='quiet',hold_minutes=60,ttl_minutes=10),
]
FILLS=['touch','sampled','through']


def append_new_day(coin,a):
    date='2026-10-09';name=f'{coin}USDT-1m-{date}.zip'
    url=f'https://data.binance.vision/data/spot/daily/klines/{coin}USDT/1m/{name}'
    cache=DEST/'new_day';cache.mkdir(exist_ok=True)
    body=get_bytes(url);check=get_bytes(url+'.CHECKSUM')
    checksum=hashlib.sha256(body).hexdigest()
    if checksum!=check.decode().split()[0]:raise ValueError('New-day checksum mismatch')
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        new=pd.read_csv(io.BytesIO(z.read(name[:-4]+'.csv')),header=None,names=RAW_COLUMNS)
    new.timestamp=new.timestamp.astype('int64')
    new.timestamp=np.where(new.timestamp>10**14,new.timestamp//1000,new.timestamp)
    if len(new)!=1440 or not (new.timestamp.diff().iloc[1:]==MINUTE).all():raise ValueError('Incomplete new-day data')
    original=pd.read_csv(ROOT/f'data/limit_minutes/{coin}_USD.csv.gz')
    if int(original.timestamp.iloc[-1])+MINUTE!=int(new.timestamp.iloc[0]):raise ValueError('New-day discontinuity')
    data=pd.concat([original,new[original.columns]],ignore_index=True)
    data['segment']=0;data['warm']=data.index>=1440
    data['ret30']=data.close.pct_change(30);data['ret60']=data.close.pct_change(60)
    data['ema60']=data.close.ewm(span=60,adjust=False,min_periods=60).mean()
    data['v24']=data.quote_volume.rolling(1440,min_periods=1440).sum()
    data['range30']=data.high.rolling(30).max()-data.low.rolling(30).min()
    (cache/name).write_bytes(body);(cache/(name+'.CHECKSUM')).write_bytes(check)
    return {c:data[c].to_numpy() for c in data.columns},{'coin':coin,'url':url,'sha256':checksum,'rows':len(new),'date':date,'source':'Binance spot USDT proxy; not Roostoo fills'}


def main():
    DEST.mkdir(parents=True,exist_ok=True)
    registration={'policies':[asdict(s) for s in SPECS],'coins':COINS,'fill_models':FILLS,
        'decision_rule':'Compare the weakest recent-period return across the three fill models; do not select on touch returns alone.',
        'caveats':['Historical development data already inspected. Nine additional policies and three coins introduce selection bias.','Fixed Roostoo tick metadata; USDT proxy; no real spread, latency, queue or partial-fill model.','Capital starts at 100000 and uses historical risk caps, not current profit/scaling latches.','Oct 9 is a new one-day chronological check, too short to establish profitability.']}
    (DEST/'registration.json').write_text(json.dumps(registration,indent=2))
    start,end=(int(pd.Timestamp(x,tz='UTC').timestamp()*1000) for x in ['2026-09-01','2026-10-09'])
    results=[];fresh=[];provenance=[]
    for coin in COINS:
        a=load(coin)
        for policy in SPECS:
            for fill in FILLS:
                spec=replace(policy,fill=fill)
                report,trades=one_coin(a,coin,1e-8,spec,start,end,True)
                results.append({'coin':coin,'spec':asdict(spec),'result':report})
                pd.DataFrame(trades).to_csv(DEST/f'{coin}_{spec.name}_{fill}.csv',index=False)
            print(json.dumps({'coin':coin,'policy':policy.name,'complete':True}),flush=True)
        try:
            extended,source=append_new_day(coin,a);provenance.append(source)
            s,e=(int(pd.Timestamp(x,tz='UTC').timestamp()*1000) for x in ['2026-10-09','2026-10-10'])
            for policy in SPECS:
                for fill in FILLS:
                    spec=replace(policy,fill=fill)
                    report,trades=one_coin(extended,coin,1e-8,spec,s,e,True)
                    fresh.append({'coin':coin,'spec':asdict(spec),'result':report})
                    pd.DataFrame(trades).to_csv(DEST/f'newday_{coin}_{spec.name}_{fill}.csv',index=False)
        except Exception as error:
            provenance.append({'coin':coin,'new_day_unavailable':type(error).__name__+': '+str(error)[:200]})
    ranked=[]
    for coin in COINS:
        for policy in SPECS:
            group=[r for r in results if r['coin']==coin and r['spec']['name']==policy.name]
            ranked.append({'coin':coin,'policy':policy.name,'minimum_return':min(r['result']['return'] for r in group),
                'models':{r['spec']['fill']:r['result'] for r in group}})
    ranked.sort(key=lambda r:r['minimum_return'],reverse=True)
    (DEST/'results.json').write_text(json.dumps({'registration':registration,'ranked':ranked,'development':results,'new_day':fresh,'new_day_provenance':provenance},indent=2,allow_nan=False))
    for row in ranked[:12]:
        print(json.dumps({'coin':row['coin'],'policy':row['policy'],'returns_pct':{fill:round(v['return']*100,5) for fill,v in row['models'].items()},'cycles':{fill:v['trades'] for fill,v in row['models'].items()}}),flush=True)


if __name__=='__main__':main()
