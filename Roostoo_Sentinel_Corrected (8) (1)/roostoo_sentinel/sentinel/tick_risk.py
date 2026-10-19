"""Persistent portfolio stops and descriptive evidence from completed net cycles.

These are client-side controls. They do not promise execution prices or future
profits. Account halts survive configuration changes and process restarts.
"""
from datetime import datetime, timezone
import json
import math
from statistics import NormalDist
from zoneinfo import ZoneInfo

import numpy as np


HKT = ZoneInfo('Asia/Hong_Kong')
EVIDENCE_VERSION = 3


def hkt_day(now):
    return datetime.fromtimestamp(now/1000, timezone.utc).astimezone(HKT).date().isoformat()


def completed_cycles(rows, mode, config_hash=None, pair=None):
    """Exclude partial exits, old evidence conventions and other accounts/policies."""
    selected = []
    seen = set()
    for row in sorted(rows, key=lambda r: int(r.get('timestamp_ms', 0))):
        if row.get('mode') != mode or not row.get('cycle_closed', True):
            continue
        if config_hash is not None and (row.get('config_hash') != config_hash or row.get('evidence_version') != EVIDENCE_VERSION):
            continue
        if pair is not None and row.get('pair') != pair:
            continue
        identity = row.get('cycle_id', row.get('order_id'))
        if identity is None or (mode, str(identity)) in seen:
            continue
        cost = float(row.get('cycle_cost', row.get('cost', 0)))
        pnl = float(row.get('cycle_pnl', row.get('pnl', 0)))
        if not math.isfinite(cost) or not math.isfinite(pnl) or cost <= 0:
            continue
        seen.add((mode, str(identity)))
        validation_pnl=float(row.get('validation_cycle_pnl',pnl))
        if not math.isfinite(validation_pnl):continue
        selected.append({**row, 'cycle_cost': cost, 'cycle_pnl': pnl, 'cycle_return': pnl/cost,'validation_cycle_return':validation_pnl/cost})
    return selected


def profitability_evidence(cycles, config, modeled_gain, modeled_loss):
    """A selection gate, not a calibrated prediction or proof of profitability.

Use a circular five-cycle block bootstrap to retain short outcome clusters.
The win-rate check also allows for modeled losses even when none was observed.
"""
    sample = cycles[-60:]
    x = np.array([r.get('validation_cycle_return',r['cycle_return']) for r in sample], dtype=float)
    n = len(x)
    wins = x[x > 0]
    losses = x[x < 0]
    gain = min(modeled_gain, float(wins.mean())) if len(wins) else modeled_gain
    loss = max(modeled_loss, float(-losses.mean())) if len(losses) else modeled_loss
    breakeven = loss/(gain+loss) if gain > 0 and loss > 0 else 1.
    win_rate = float((x > 0).mean()) if n else 0.
    # One-sided 90% Wilson lower bound, with modeled loss rather than loss=0.
    z = NormalDist().inv_cdf(.90)
    lower_win = (win_rate+z*z/(2*n)-z*math.sqrt(win_rate*(1-win_rate)/n+z*z/(4*n*n)))/(1+z*z/n) if n else 0.
    lower_mean = None
    if n >= config.probe_cycles:
        rng = np.random.default_rng(918)
        block = min(5, n)
        starts = rng.integers(0, n, (2000, math.ceil(n/block)))
        indexes = ((starts[..., None]+np.arange(block)) % n).reshape(2000, -1)[:, :n]
        lower_mean = float(np.quantile(x[indexes].mean(axis=1), .10))
    required_win = max(config.probe_min_win_rate, breakeven+config.probe_win_rate_margin)
    verified = (n >= config.probe_cycles and gain > 0 and win_rate >= required_win
                and lower_win > breakeven and lower_mean is not None and lower_mean > 0)
    return {'completed_cycles_for_policy': len(cycles), 'window_cycles': n,
            'verified_for_scaling': bool(verified), 'win_rate': win_rate,
            'win_rate_lower_90pct': lower_win, 'breakeven_win_rate': breakeven,
            'required_observed_win_rate': required_win,
            'mean_net_cycle_return': float(x.mean()) if n else None,
            'mean_return_block_lower_90pct': lower_mean,
            'modeled_win_return': modeled_gain, 'modeled_loss_return': modeled_loss,
            'conservative_win_return': gain, 'conservative_loss_return': loss,
            'observed_worst_loss_return': float(-losses.min()) if len(losses) else 0.,
            'conservative_expectancy_at_lower_win_rate': lower_win*gain-(1-lower_win)*loss,
            'profit_factor': float(wins.sum()/-losses.sum()) if len(losses) else None,
            'evidence_source': 'completed_cycles_for_current_mode_and_config',
            'confidence_caveat': 'Descriptive dependent-sample checks; no future-return guarantee.'}


class TickRiskEngine:
    def __init__(self, config, store, mode):
        self.c, self.store, self.mode = config, store, mode

    def baseline(self, counters, equity):
        if counters.get('initial_equity') is not None:
            return float(counters['initial_equity']), counters.get('initial_equity_source', 'persisted')
        # Preserve the earliest recorded account mark when upgrading an existing
        # ledger. Restarting on a profitable day must not silently reset the goal.
        for (payload,) in self.store.db.execute("SELECT payload FROM events WHERE kind='tick_portfolio' ORDER BY id"):
            row = json.loads(payload)
            value = row.get('liquidation_equity', row.get('equity'))
            if row.get('mode', self.mode) == self.mode and value is not None and math.isfinite(float(value)) and float(value) > 0:
                return float(value), 'earliest_recorded_portfolio'
        return float(counters.get('start_equity', equity)), 'first_observed_equity'

    def evaluate(self, equity, now, cycles, evidence):
        if not math.isfinite(equity) or equity < 0:
            raise ValueError('Invalid portfolio equity for risk evaluation')
        counters = self.store.get('tick_counters', {})
        initial, source = self.baseline(counters, equity)
        if not math.isfinite(initial) or initial <= 0:
            raise ValueError('Risk baseline must be positive')
        day = hkt_day(now)
        if counters.get('day') != day:
            counters = {**counters, 'day': day, 'start_equity': equity,
                        'daily_halted': False, 'daily_profit_halted': False,
                        'loss_streak_halted': False}
        counters.update(initial_equity=initial, initial_equity_source=source)
        counters['peak'] = max(equity, float(counters.get('peak', equity)))
        daily_start = float(counters['start_equity'])
        today = [r for r in cycles if hkt_day(int(r['timestamp_ms'])) == day]
        streak = 0
        for row in reversed(today):
            if row['cycle_pnl'] >= 0:
                break
            streak += 1
        conditions = {
            'absolute_loss_halted': (equity <= initial*(1-self.c.max_total_loss)+1e-8, 'total_loss_limit'),
            'halted': (equity <= counters['peak']*(1-self.c.max_drawdown)+1e-8, 'drawdown_limit'),
            'daily_halted': (equity <= daily_start*(1-self.c.daily_loss)+1e-8, 'daily_loss_limit'),
            'loss_streak_halted': (streak >= self.c.max_consecutive_losses, 'consecutive_loss_limit'),
            'total_profit_halted': (equity >= initial*(1+self.c.total_profit_target)-1e-8, 'total_profit_target'),
            'daily_profit_halted': (daily_start > 0 and equity >= daily_start*(1+self.c.daily_profit_target)-1e-8, 'daily_profit_target'),
        }
        failed = []
        for pair, result in evidence.items():
            n, mean = result['window_cycles'], result['mean_net_cycle_return']
            if n >= self.c.probe_cycles and mean is not None and mean <= 0:
                failed.append({'pair': pair, 'reason': 'nonpositive_net_expectancy', **result})
            elif result['completed_cycles_for_policy'] >= self.c.probe_max_cycles and not result['verified_for_scaling']:
                failed.append({'pair': pair, 'reason': 'profitability_not_demonstrated', **result})
        conditions['performance_halted'] = (bool(failed), 'profitability_check_failed')
        new_halts = []
        for flag, (breached, reason) in conditions.items():
            if breached and not counters.get(flag, False):
                counters[flag] = True
                new_halts.append(reason)
        reasons = [reason for flag, (_, reason) in conditions.items() if counters.get(flag, False)]
        counters['last_equity'] = equity
        counters['last_checked_ms'] = now
        # Persist the latch before attempting any cancel or sell.
        self.store.set('tick_counters', counters)
        status = {'mode': self.mode, 'halted': bool(reasons), 'halt_reasons': reasons, 'new_halts': new_halts,
                  'day_hkt': day, 'liquidation_equity': equity,
                  'initial_equity': initial, 'initial_equity_source': source,
                  'day_start_equity': daily_start, 'peak_equity': counters['peak'],
                  'total_pnl_usd': equity-initial, 'daily_pnl_usd': equity-daily_start,
                  'total_return': equity/initial-1,
                  'daily_return': equity/daily_start-1 if daily_start > 0 else None,
                  'drawdown': equity/counters['peak']-1 if counters['peak'] > 0 else None,
                  'consecutive_losses_today': streak,
                  'thresholds': {'total_profit_equity': initial*(1+self.c.total_profit_target),
                                 'daily_profit_equity': daily_start*(1+self.c.daily_profit_target),
                                 'total_loss_equity': initial*(1-self.c.max_total_loss),
                                 'daily_loss_equity': daily_start*(1-self.c.daily_loss),
                                 'drawdown_equity': counters['peak']*(1-self.c.max_drawdown)},
                  'profitability_failures': failed}
        self.store.set('tick_risk_status', status)
        self.store.set('tick_profitability', evidence)
        if new_halts:
            self.store.event('tick_risk_halt', {'mode': self.mode, **status}, now)
        return status
