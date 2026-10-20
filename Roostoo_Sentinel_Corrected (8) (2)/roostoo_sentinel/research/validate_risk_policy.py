"""Conditional risk-overlay diagnostic on archived simulated trade streams.

This is NOT an execution backtest of the corrected best-bid bot. It reuses old
entry/exit outcomes, ignores intratrade marks, and cannot establish Roostoo fills.
The purpose is to inspect when the new risk and sizing gates would refuse risk.
"""
from dataclasses import asdict
import contextlib
import csv
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sentinel.state import Store
from sentinel.tick import TickConfig, net_cycle
from sentinel.tick_risk import TickRiskEngine, hkt_day, profitability_evidence


def replay(path, config):
    with path.open(newline='') as stream:
        trades = list(csv.DictReader(stream))
    cash, completed, scaled, probes, skips = 100000., [], 0, 0, {}
    final_risk = None
    with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
        store = Store(Path(temp)/'research_only.sqlite3')
        risk = TickRiskEngine(config, store, 'shadow')
        try:
            for n, row in enumerate(trades):
                entry, qty = float(row['entry']), float(row['quantity'])
                entry_time, exit_time = int(row['entry_ts']), int(row['exit_ts'])
                modeled_gain = net_cycle(entry, entry+1e-8*config.target_ticks, config.maker_fee)
                modeled_loss = (config.stop_ticks+config.exit_slippage_ticks)*1e-8/entry+config.maker_fee+config.taker_fee
                evidence = profitability_evidence(completed, config, modeled_gain, modeled_loss)
                final_risk = risk.evaluate(cash, entry_time, completed, {'PEPE/USD': evidence})
                reason = final_risk['halt_reasons'][0] if final_risk['halted'] else None
                if not reason and sum(hkt_day(r['timestamp_ms']) == hkt_day(entry_time) for r in completed) >= config.max_cycles_per_day:
                    reason = 'daily_completed_cycle_limit'
                if reason:
                    skips[reason] = skips.get(reason, 0)+1
                    continue
                loss = max(modeled_loss, evidence['observed_worst_loss_return'])
                notional = min(cash*config.allocation_cap, cash*config.planned_risk/loss, cash/(1+config.maker_fee))
                if not evidence['verified_for_scaling']:
                    notional = min(notional, config.probe_notional)
                    probes += 1
                else:
                    scaled += 1
                original_return = float(row['pnl'])/(entry*qty*(1+config.maker_fee))
                cost = int(notional/entry)*entry*(1+config.maker_fee)
                pnl = original_return*cost
                cash += pnl
                completed.append({'cycle_id': n, 'timestamp_ms': exit_time, 'cycle_cost': cost,
                                  'cycle_pnl': pnl, 'cycle_return': pnl/cost,
                                  'mode': 'shadow', 'pair': 'PEPE/USD', 'config_hash': config.fingerprint,
                                  'evidence_version': 2, 'cycle_closed': True})
                evidence = profitability_evidence(completed, config, modeled_gain, modeled_loss)
                final_risk = risk.evaluate(cash, exit_time, completed, {'PEPE/USD': evidence})
        finally:
            store.close()
    return {'source': str(path.relative_to(ROOT)), 'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'source_simulated_cycles': len(trades), 'accepted_cycles': len(completed),
            'probe_cycles': probes, 'larger_cycles': scaled, 'skipped_cycles_by_first_reason': skips,
            'conditional_cash_return': cash/100000-1, 'conditional_cash_pnl_usd': cash-100000,
            'final_halt_reasons': final_risk['halt_reasons'] if final_risk else []}


def main():
    config = TickConfig.load(ROOT/'tick_config.json')
    result = {'execution_backtest': False, 'authenticated_account_results': False,
              'caveat': 'Conditional overlay of archived simulated entry/exit returns, not measured performance of the best-bid execution policy. No intratrade marks, prospective holdout, latency, executable spread, partial fills or new signal generation. Skipping trades changes the real future signal sequence; this replay cannot reproduce that effect.',
              'config': asdict(config),
              'streams': [replay(ROOT/f'research/limit_research/PEPE_{name}_trades.csv', config) for name in ('touch', 'sampled', 'through')]}
    destination = ROOT/'research/risk_overlay.json'
    destination.write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    for row in result['streams']:
        print(json.dumps(row, allow_nan=False))


if __name__ == '__main__': main()
