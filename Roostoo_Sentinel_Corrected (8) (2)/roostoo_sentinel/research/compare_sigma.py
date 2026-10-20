"""Small pre-recorded ablation set. Freeze validation ranking before 2026 test."""
import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sentinel.data import read_bars
from sentinel.sigma import SigmaConfig, candidates, prepare
from sentinel.sigma_backtest import export, run

ROOT=Path('research/sigma_gate')


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--stage',choices=['develop','test','stress','episodes'],required=True)
    a=ap.parse_args()
    cfg=SigmaConfig()
    bars={p:read_bars(cfg.data_dir,p) for p in cfg.pairs}
    ROOT.mkdir(parents=True,exist_ok=True)
    memo={}
    def features(c):
        key=(c.gate_lookbacks,c.vol_halflife,c.fast_vol_halflife,c.cov_window,c.cov_shrink)
        if key not in memo:memo[key]=prepare(bars,c)
        return memo[key]
    rows=[]
    if a.stage=='develop':
        periods={'development':('2022-03-01T00:00:00Z','2025-01-01T00:00:00Z'),'validation':('2025-01-01T00:00:00Z','2026-01-01T00:00:00Z')}
        for c in candidates():
            for period,(start,end) in periods.items():
                for model in ('passive','all_taker'):
                    res=run(features(c),c,start,end,model)
                    path=ROOT/'comparisons'/c.name/period/model
                    export(res,path)
                    row={'candidate':c.name,'period':period,**res.summary}
                    rows.append(row)
                    print(json.dumps({k:row[k] for k in ('candidate','period','model','return','sharpe','max_drawdown','fees_usd','trades')}),flush=True)
        frame=pd.DataFrame(rows)
        frame.to_csv(ROOT/'development_validation.csv',index=False)
        rank=[]
        for c in candidates():
            if c.fixed_long:continue
            v=frame[(frame.candidate==c.name)&(frame.period=='validation')]
            d=frame[(frame.candidate==c.name)&(frame.period=='development')]
            # Require positive return in both periods under both fee models.
            passes=bool((v['return']>0).all() and (d['return']>0).all())
            score=float(min(v.sharpe.min(),d.sharpe.min())-2*v.max_drawdown.max())
            rank.append({'name':c.name,'passes':passes,'score':score,'worst_validation_return':float(v['return'].min())})
        rank.sort(key=lambda x:(x['passes'],x['score']),reverse=True)
        chosen=rank[0]['name']
        selected=next(c for c in candidates() if c.name==chosen)
        freeze={'selection_inputs':'2022–2024 development and calendar 2025 validation only','ranking':rank,'selected':asdict(selected),'selected_hash':selected.fingerprint,'profitability_proven':False}
        (ROOT/'selection_frozen.json').write_text(json.dumps(freeze,indent=2))
        print(json.dumps({'selected_before_2026_test':chosen,'passes_prerecorded_screen':rank[0]['passes']}),flush=True)
        return
    frozen=json.loads((ROOT/'selection_frozen.json').read_text())
    x=frozen['selected'];x['pairs']=tuple(x['pairs']);x['gate_lookbacks']=tuple(x['gate_lookbacks'])
    selected=SigmaConfig(**x)
    chosen={c.name:c for c in [candidates()[0],candidates()[1],selected,candidates()[-1]]}
    if a.stage in {'test','stress'}:
        periods={'test_2026':('2026-01-01T00:00:00Z','2026-10-10T00:00:00Z'),'recent_october':('2026-10-03T00:00:00Z','2026-10-10T00:00:00Z')}
        models=('passive','all_taker') if a.stage=='test' else ('double_cost','delayed')
        for c in chosen.values():
            for period,(start,end) in periods.items():
                for model in models:
                    res=run(features(c),c,start,end,model)
                    export(res,ROOT/'comparisons'/c.name/period/model)
                    row={'candidate':c.name,'period':period,**res.summary};rows.append(row)
                    print(json.dumps({k:row[k] for k in ('candidate','period','model','return','sharpe','max_drawdown','fees_usd')}),flush=True)
        pd.DataFrame(rows).to_csv(ROOT/(a.stage+'_results.csv'),index=False)
        if a.stage=='stress':
            for phase in range(selected.sizing_hours):
                c=replace(selected,sizing_phase=phase)
                res=run(features(c),c,'2026-01-01T00:00:00Z','2026-10-10T00:00:00Z','all_taker')
                rows.append({'candidate':c.name,'phase':phase,**res.summary})
                print(json.dumps({'phase':phase,**{k:res.summary[k] for k in ('return','sharpe','max_drawdown')}}),flush=True)
            pd.DataFrame([r for r in rows if 'phase' in r]).to_csv(ROOT/'phase_stress.csv',index=False)
        return
    # Fresh state and capital for each NON-overlapping contest; includes entries,
    # liquidation fees and the actual persistent/daily risk controls.
    for c in chosen.values():
        start=pd.Timestamp('2026-01-01T00:00:00Z')
        finish=pd.Timestamp('2026-10-10T00:00:00Z')
        while start+pd.Timedelta(days=14)<=finish:
            end=start+pd.Timedelta(days=14)
            res=run(features(c),c,start,end,'all_taker',competition_stops=True)
            counts=pd.to_datetime(res.trades.timestamp,utc=True).dt.tz_convert('Asia/Hong_Kong').dt.date.value_counts() if len(res.trades) else pd.Series(dtype=int)
            rows.append({'candidate':c.name,'episode_start':start.isoformat(),'days_with_at_least_2_simulated_fills':int((counts>=2).sum()),**res.summary})
            start=end
        subset=[r for r in rows if r['candidate']==c.name]
        print(json.dumps({'candidate':c.name,'episodes':len(subset),'positive_fraction':float(np.mean([r['return']>0 for r in subset])),'median_return':float(np.median([r['return'] for r in subset])),'greater_than_1pct':float(np.mean([r['return']>.01 for r in subset]))}),flush=True)
    pd.DataFrame(rows).to_csv(ROOT/'independent_14day_episodes.csv',index=False)


if __name__=='__main__':main()
