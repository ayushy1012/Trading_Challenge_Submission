"""Run the actual engine on recorded public Roostoo snapshots, without orders.

This compares an optimistic last-price fill assumption with executable quotes.
Both are local shadow simulations. Neither is a measured account return.
"""
import argparse
from collections import Counter
import contextlib
from dataclasses import asdict,replace
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sentinel.state import Store
from sentinel.tick import TickConfig,TickEngine

ROOT=Path(__file__).resolve().parents[1]


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--quotes',default=str(ROOT/'research/execution_validation/quote_observations.jsonl'))
    args=ap.parse_args();path=Path(args.quotes)
    rows=[json.loads(line) for line in path.read_text().splitlines()]
    rows.sort(key=lambda r:r['collected_ms'])
    info=json.loads((ROOT/'research/mock_readonly_baseline.json').read_text())['exchange_info']
    primary=TickConfig.load(ROOT/'tick_config.json')
    profiles=[('current_quiet_pullback',primary),
              ('best_bid_with_executable_quotes',replace(primary,entry_reference='bid',regime_filter='stable',require_quote_movement=False)),
              ('best_bid_with_last_price_touches',replace(primary,entry_reference='bid',regime_filter='stable',require_quote_movement=False,shadow_fill_reference='last')),
              ('watchlist_quiet_pullback',TickConfig.load(ROOT/'tick_config_watchlist.json'))]
    results=[]
    for name,cfg in profiles:
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            cfg=replace(cfg,runtime_dir=directory)
            store=Store(Path(directory)/'shadow.sqlite3');engine=TickEngine(cfg,None,store,info,'shadow')
            for row in rows:engine.step(row['ticker'],int(row['collected_ms']))
            mark=store.get('tick_last_snapshot',{});samples=store.get('tick_closed_samples',[])
            decisions=[json.loads(r[0]) for r in store.db.execute("SELECT payload FROM events WHERE kind='tick_decision'")]
            results.append({'profile':name,'config':asdict(cfg),'sample_count':len(rows),'duration_seconds':(rows[-1]['collected_ms']-rows[0]['collected_ms'])/1000,
                'filled_orders':len(store.fills()),'completed_cycles':sum(x.get('cycle_closed',True) for x in samples),
                'allocated_realized_pnl':sum(x['pnl'] for x in samples),'equity':mark.get('equity'),'end_state':engine.state(),
                'last_decision':store.get('tick_last_decision'),'execution_source':'local_shadow_simulation; no account writes'})
            results[-1]['decision_counts']=dict(Counter(r['reason'] for r in decisions))
            results[-1]['pair_skip_counts']=dict(Counter(p['reason'] for r in decisions for p in r.get('pair_checks',[])))
            store.close()
    output={'quote_source':str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else path.name,'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'caveat':'Short, single-session observation. Snapshots miss intervening quotes, depth, queue and latency; all replay fills and P&L are simulated. Live limit testing separately rejects last-price-touch as a sufficient fill rule. This replay establishes no profitable strategy.',
        'results':results}
    destination=ROOT/'research/execution_validation/quote_replay.json';destination.write_text(json.dumps(output,indent=2,allow_nan=False))
    for r in results:print(json.dumps({k:r[k] for k in ['profile','sample_count','duration_seconds','filled_orders','completed_cycles','allocated_realized_pnl','equity','last_decision']}),flush=True)


if __name__=='__main__':main()
