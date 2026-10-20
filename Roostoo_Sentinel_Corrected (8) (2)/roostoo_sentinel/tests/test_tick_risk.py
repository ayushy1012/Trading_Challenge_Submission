"""Synthetic account regressions; none of these are exchange-profit evidence."""
import contextlib
from dataclasses import asdict, replace
import io
import json
from pathlib import Path
import tempfile
import unittest

from sentinel.state import Store
from sentinel.tick import TickConfig, TickEngine
from sentinel.tick_report import export_report, snapshot
from sentinel.tick_risk import TickRiskEngine, completed_cycles, hkt_day, profitability_evidence
from test_tick_corrections import META, NOW, Venue, quotes


def cycles(returns, config, start=NOW):
    return [{'order_id': n, 'cycle_id': n, 'timestamp_ms': start+n*1000,
             'mode': 'paper-exchange', 'pair': 'PEPE/USD', 'config_hash': config.fingerprint,
             'evidence_version': 3, 'cycle_closed': True, 'cost': 100., 'pnl': value*100,
             'cycle_cost': 100., 'cycle_pnl': value*100, 'cycle_return': value}
            for n, value in enumerate(returns)]


class ExitVenue(Venue):
    def __init__(self):
        super().__init__()
        self.cancel_fill = False
        self.exit_price = 3.88e-6

    def cancel_order(self, identity):
        result = super().cancel_order(identity)
        if self.cancel_fill:
            self.fill(next(r for r in self.orders if r['OrderID'] == identity))
        return result

    def place(self, pair, side, quantity):
        self.writes.append(('MARKET', side))
        assert side == 'SELL'
        qty, px = float(quantity), self.exit_price
        row = {'OrderID': len(self.orders)+1, 'Pair': pair, 'Side': side, 'Type': 'MARKET',
               'Quantity': qty, 'Price': px, 'CreateTimestamp': self.now,
               'Status': 'FILLED', 'FilledQuantity': qty, 'FilledAverPrice': px,
               'FinishTimestamp': self.now, 'Role': 'TAKER', 'CommissionPercent': .001,
               'CommissionCoin': 'USD', 'CommissionChargeValue': qty*px*.001}
        self.orders.append(row)
        self.wallet['USD']['Free'] += qty*px*.999
        self.wallet['PEPE']['Free'] -= qty
        return {'Success': True, 'OrderDetail': row.copy()}


class RiskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root/'tick_paper_exchange.sqlite3')
        self.c = replace(TickConfig(), runtime_dir=str(self.root))
        self.risk = TickRiskEngine(self.c, self.store, 'paper-exchange')
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()

    def tearDown(self):
        self.output.__exit__(None, None, None)
        self.store.close()
        self.tmp.cleanup()

    def check(self, equity, now=NOW, rows=(), evidence=None):
        return self.risk.evaluate(equity, now, rows, evidence or {})

    def engine(self):
        api = ExitVenue()
        engine = TickEngine(self.c, api, self.store, META, 'paper-exchange')
        engine.quotes = quotes()
        self.store.set('tick_quote_history', [{'t': NOW-n*30000, 'quotes': {'PEPE/USD': 3.89e-6}} for n in range(10, 0, -1)])
        return engine, api

    def test_daily_profit_stop_is_inclusive_and_resets_only_next_hkt_day(self):
        self.check(100000)
        result = self.check(100350, NOW+30000)
        self.assertEqual(result['halt_reasons'], ['daily_profit_target'])
        self.assertEqual(result['thresholds']['daily_profit_equity'], 100350)
        self.assertTrue(self.check(100200, NOW+60000)['halted'])
        self.assertFalse(self.check(100200, NOW+86400000)['halted'])

    def test_total_profit_stop_survives_restart_recovery_and_day_rollover(self):
        self.check(100000)
        self.assertIn('total_profit_target', self.check(101100, NOW+30000)['halt_reasons'])
        self.risk = TickRiskEngine(self.c, self.store, 'paper-exchange')
        result = self.check(100900, NOW+86400000)
        self.assertEqual(result['halt_reasons'], ['total_profit_target'])
        self.assertEqual(result['initial_equity'], 100000)

    def test_risk_events_are_emitted_once_per_new_latch(self):
        self.check(100000)
        self.check(100350, NOW+30000)
        self.check(100100, NOW+60000)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM events WHERE kind='tick_risk_halt'").fetchone()[0], 1)

    def test_absolute_loss_stop_survives_new_day_even_after_daily_limit_resets(self):
        self.check(100000)
        self.check(99000, NOW+30000)
        self.check(98200, NOW+86400000)
        result = self.check(97500, NOW+86430000)
        self.assertIn('total_loss_limit', result['halt_reasons'])
        self.assertIn('drawdown_limit', result['halt_reasons'])
        self.assertIn('total_loss_limit', self.check(100000, NOW+2*86400000)['halt_reasons'])

    def test_drawdown_uses_peak_independently_of_starting_account_loss(self):
        self.check(100000)
        self.check(100300, NOW+30000)
        result = self.check(97792.5, NOW+60000)
        self.assertIn('drawdown_limit', result['halt_reasons'])
        self.assertNotIn('total_loss_limit', result['halt_reasons'])

    def test_daily_loss_boundary_is_inclusive(self):
        self.check(100000)
        self.assertEqual(self.check(99250, NOW+30000)['halt_reasons'], ['daily_loss_limit'])

    def test_three_losing_completed_cycles_pause_the_day(self):
        self.check(100000)
        rows = cycles([.001, -.005, -.007, -.002], self.c)
        result = self.check(99999, NOW+30000, rows)
        self.assertEqual(result['consecutive_losses_today'], 3)
        self.assertEqual(result['halt_reasons'], ['consecutive_loss_limit'])
        self.assertFalse(self.check(99999, NOW+86400000, rows)['halted'])

    def test_winning_cycle_breaks_loss_streak_and_partial_exits_do_not_count(self):
        self.check(100000)
        rows = cycles([-.01, -.01, .001, -.01], self.c)
        partial = {**rows[-1], 'cycle_id': 'partial', 'cycle_closed': False, 'pnl': -1}
        completed = completed_cycles([*rows, partial], 'paper-exchange', self.c.fingerprint)
        result = self.check(99990, NOW+30000, completed)
        self.assertFalse(result['halted'])
        self.assertEqual(result['consecutive_losses_today'], 1)

    def test_upgrade_uses_earliest_ledger_equity_without_resetting_profit_reference(self):
        self.store.set('tick_counters', {'day': hkt_day(NOW), 'start_equity': 100700, 'peak': 100700, 'halted': False})
        self.store.event('tick_portfolio', {'mode': 'shadow', 'equity': 500000}, NOW-300000)
        self.store.event('tick_portfolio', {'mode': 'paper-exchange', 'equity': 100000}, NOW-200000)
        result = self.check(101100)
        self.assertEqual(result['initial_equity'], 100000)
        self.assertEqual(result['initial_equity_source'], 'earliest_recorded_portfolio')
        self.assertIn('total_profit_target', result['halt_reasons'])

    def test_loss_latches_survive_config_changes(self):
        self.check(100000)
        self.check(97500, NOW+30000)
        changed = replace(self.c, max_total_loss=.05, total_profit_target=.02)
        risk = TickRiskEngine(changed, self.store, 'paper-exchange')
        self.assertIn('total_loss_limit', risk.evaluate(100000, NOW+86400000, [], {})['halt_reasons'])

    def test_hkt_boundary_is_used_for_daily_halts(self):
        # 15:59 and 16:00 UTC straddle midnight Hong Kong.
        from datetime import datetime, timezone
        before = int(datetime(2026, 10, 10, 15, 59, tzinfo=timezone.utc).timestamp()*1000)
        self.check(100000, before)
        self.assertTrue(self.check(100350, before+10000)['halted'])
        self.assertFalse(self.check(100350, before+60000)['halted'])

    def test_nonfinite_equity_is_rejected(self):
        for value in (float('nan'), float('inf'), -1):
            with self.assertRaises(ValueError): self.check(value)

    def test_positive_net_completed_cycles_are_required_for_larger_orders(self):
        engine, _ = self.engine()
        self.assertFalse(engine.probe_verified('PEPE/USD'))
        for n in range(20): engine.close_sample('PEPE/USD', .16, 100, NOW+n, order_id=n)
        evidence = engine.profitability('PEPE/USD')
        self.assertTrue(evidence['verified_for_scaling'])
        self.assertGreater(evidence['win_rate_lower_90pct'], evidence['breakeven_win_rate'])
        self.assertGreater(evidence['mean_return_block_lower_90pct'], 0)

    def test_95_percent_win_rate_and_positive_mean_are_insufficient_if_loss_confidence_is_weak(self):
        data = cycles([.01]*19+[-.001], self.c)
        result = profitability_evidence(data, self.c, .001575, .01181)
        self.assertGreater(result['mean_net_cycle_return'], 0)
        self.assertGreater(result['mean_return_block_lower_90pct'], 0)
        self.assertFalse(result['verified_for_scaling'])
        self.assertLess(result['win_rate_lower_90pct'], result['breakeven_win_rate'])

    def test_unverified_positive_mean_still_places_only_a_hundred_dollar_probe(self):
        engine, api = self.engine()
        self.store.set('tick_closed_samples', cycles([.01]*19+[-.001], self.c, NOW-60000))
        engine.step(quotes(), NOW)
        self.assertEqual(len(api.orders), 1)
        self.assertLessEqual(api.orders[0]['Price']*api.orders[0]['Quantity'], 100)
        self.assertFalse(self.store.get('tick_last_decision')['profitability']['verified_for_scaling'])

    def test_verified_policy_can_scale_but_keeps_stop_risk_and_allocation_caps(self):
        engine, api = self.engine()
        self.store.set('tick_closed_samples', cycles([.0016]*20, self.c, NOW-60000))
        engine.step(quotes(), NOW)
        notional = api.orders[0]['Price']*api.orders[0]['Quantity']
        evidence = engine.profitability('PEPE/USD')
        self.assertGreater(notional, 100)
        self.assertLessEqual(notional, 100000*self.c.allocation_cap)
        self.assertLessEqual(notional*evidence['modeled_loss_return'], 100000*self.c.planned_risk)

    def test_negative_expectancy_blocks_real_order_path(self):
        engine, api = self.engine()
        self.store.set('tick_closed_samples', cycles([.001]*18+[-.02]*2, self.c, NOW-60000))
        engine.step(quotes(), NOW)
        self.assertEqual(api.writes, [])
        self.assertEqual(self.store.get('tick_last_decision')['reason'], 'profitability_check_failed')

    def test_clustered_losses_have_nonpositive_block_lower_mean(self):
        result = profitability_evidence(cycles([.002]*45+[-.004]*15, self.c), self.c, .002, .004)
        self.assertGreater(result['mean_net_cycle_return'], 0)
        self.assertLess(result['mean_return_block_lower_90pct'], 0)
        self.assertFalse(result['verified_for_scaling'])

    def test_negative_net_expectancy_halts_instead_of_increasing_probe_size(self):
        self.check(100000)
        data = cycles([.001]*18+[-.02]*2, self.c)
        evidence = {'PEPE/USD': profitability_evidence(data, self.c, .001575, .01181)}
        result = self.check(99990, NOW+30000, data, evidence)
        self.assertEqual(result['halt_reasons'], ['profitability_check_failed'])
        self.assertEqual(result['profitability_failures'][0]['reason'], 'nonpositive_net_expectancy')
        self.assertIn('profitability_check_failed', self.check(100000, NOW+86400000)['halt_reasons'])

    def test_unverified_probe_budget_expires_at_sixty_completed_cycles(self):
        self.check(100000)
        data = cycles([.0002]*60, self.c)
        evidence = {'PEPE/USD': profitability_evidence(data, self.c, .001575, .01181)}
        result = self.check(100001, NOW+60000, data, evidence)
        self.assertEqual(result['profitability_failures'][0]['reason'], 'profitability_not_demonstrated')

    def test_old_policy_shadow_partial_and_duplicate_cycles_cannot_validate_new_policy(self):
        good = cycles([.002], self.c)[0]
        rows = [good, good.copy(), {**good, 'cycle_id': 2, 'mode': 'shadow'},
                {**good, 'cycle_id': 3, 'config_hash': 'old'},
                {**good, 'cycle_id': 4, 'cycle_closed': False},
                {**good, 'cycle_id': 5, 'evidence_version': 1},
                {**good, 'cycle_id': 6, 'evidence_version': 2}]
        self.assertEqual(len(completed_cycles(rows, 'paper-exchange', self.c.fingerprint)), 1)

    def test_losing_partial_exit_and_winning_final_exit_form_one_losing_cycle(self):
        engine, _ = self.engine()
        pos = {'qty': 100/3.88e-6, 'cost': 100, 'entry': 3.88e-6, 'cycle_id': 1, 'config_hash': self.c.fingerprint}
        first, remaining = engine.sale_sample(pos, 'PEPE/USD', pos['qty']/2, pos['qty']/2, -2, NOW, {'id': 2})
        second, leftover = engine.sale_sample(remaining, 'PEPE/USD', remaining['qty'], 0, .1, NOW+30000, {'id': 3})
        data = completed_cycles([first, second], 'paper-exchange', self.c.fingerprint)
        self.assertIsNone(leftover)
        self.assertEqual(len(data), 1)
        self.assertAlmostEqual(data[0]['cycle_cost'], 100)
        self.assertAlmostEqual(data[0]['cycle_pnl'], -1.9)

    def test_unsellable_dust_is_not_counted_as_free_profit_in_validation(self):
        engine, _ = self.engine()
        pos = {'qty': 100/3.88e-6, 'cost': 100, 'entry': 3.88e-6, 'cycle_id': 1, 'config_hash': self.c.fingerprint}
        sample, remaining = engine.sale_sample(pos, 'PEPE/USD', pos['qty']*.95, pos['qty']*.05, .1, NOW, {'id': 2})
        self.assertIsNone(remaining)
        self.assertAlmostEqual(sample['pnl'], .1)
        self.assertAlmostEqual(sample['cycle_pnl'], -4.9)
        self.assertAlmostEqual(sample['dust_cost_writeoff_usd'], 5)

    def test_profit_halt_cancels_buy_without_placing_another_order(self):
        engine, api = self.engine()
        engine.step(quotes(), NOW)
        api.wallet['USD']['Free'] = 101100
        engine.step(quotes(), NOW+30000)
        self.assertTrue(self.store.get('tick_counters')['total_profit_halted'])
        self.assertEqual(api.writes, [('LIMIT', 'BUY'), ('CANCEL', 1)])
        api.orders[0]['Status'] = 'CANCELED'
        engine.step(quotes(), NOW+60000)
        self.assertEqual(engine.state()['phase'], 'idle')
        engine.step(quotes(), NOW+86400000)
        self.assertEqual(len(api.writes), 2)

    def target_pending(self, engine, api):
        engine.step(quotes(), NOW)
        api.now = NOW+30000
        api.fill(api.orders[0])
        engine.step(quotes(), NOW+30000)
        self.assertEqual(engine.state()['phase'], 'target')

    def test_profit_halt_waits_for_cancel_reconciliation_then_liquidates_exactly_once(self):
        engine, api = self.engine()
        self.target_pending(engine, api)
        api.wallet['USD']['Free'] += 1200  # Synthetic balance change to exercise the halt.
        api.now = NOW+60000
        engine.step(quotes(), NOW+60000)
        self.assertEqual(api.writes[-1], ('CANCEL', 2))
        self.assertFalse(any(kind == 'MARKET' for kind, _ in api.writes))
        self.assertTrue(self.store.get('tick_counters')['total_profit_halted'])
        api.orders[1]['Status'] = 'CANCELED'
        api.now = NOW+90000
        engine.step(quotes(), NOW+90000)
        self.assertEqual(api.writes[-1], ('MARKET', 'SELL'))
        engine.step(quotes(), NOW+120000)
        self.assertIsNone(engine.state()['position'])
        self.assertEqual(sum(kind == 'MARKET' for kind, _ in api.writes), 1)
        self.assertEqual(sum(side == 'BUY' for _, side in api.writes), 1)

    def test_sell_filled_during_profit_halt_cancel_does_not_cause_duplicate_market_sell(self):
        engine, api = self.engine()
        self.target_pending(engine, api)
        api.wallet['USD']['Free'] += 1200
        api.cancel_fill = True
        engine.step(quotes(), NOW+60000)
        engine.step(quotes(), NOW+90000)
        self.assertIsNone(engine.state()['position'])
        self.assertFalse(any(kind == 'MARKET' for kind, _ in api.writes))

    def test_profit_stop_marks_inventory_at_bid_less_slippage_and_taker_fee(self):
        engine, api = self.engine()
        engine.step(quotes(), NOW)
        api.now = NOW+30000
        api.fill(api.orders[0])
        engine.step(quotes(), NOW+30000)
        report = self.store.get('tick_last_snapshot')
        qty = api.wallet['PEPE']['Free']
        expected = api.wallet['USD']['Free']+qty*(3.88e-6-1e-8)*.999
        self.assertAlmostEqual(report['liquidation_equity'], expected)
        self.assertLess(report['liquidation_equity'], report['equity'])

    def test_config_rejects_disabled_or_inconsistent_risk_controls(self):
        path = self.root/'tick.json'
        for values in ({'total_profit_target': 0}, {'daily_profit_target': .02},
                       {'max_total_loss': .001}, {'max_consecutive_losses': 0},
                       {'exit_slippage_ticks': -1}, {'probe_max_cycles': 19},
                       {'probe_max_cycles': 61}, {'probe_win_rate_margin': -.1}):
            path.write_text(json.dumps({**asdict(self.c), **values}))
            with self.assertRaises(ValueError): TickConfig.load(path)

    def test_reports_show_halt_reason_threshold_and_profitability_status(self):
        engine, api = self.engine()
        engine.step(quotes(), NOW)
        api.wallet['USD']['Free'] = 101100
        engine.step(quotes(), NOW+30000)
        result = export_report(self.store.db, 'paper-exchange', self.root/'reports', NOW+30000)
        self.assertIn('total_profit_target', result['risk']['halt_reasons'])
        self.assertAlmostEqual(result['risk']['thresholds']['total_profit_equity'], 101100)
        self.assertIn('PEPE/USD', result['profitability'])
        self.assertIn('larger trades permitted=False', (self.root/'reports/summary.txt').read_text())
        shadow = snapshot(self.store.db, 'shadow', NOW+30000)
        self.assertEqual(shadow['risk'], {})
        self.assertEqual(shadow['profitability'], {})

    def test_profit_halt_reports_missing_active_days_without_inventing_activity(self):
        engine, api = self.engine()
        engine.step(quotes(), NOW)
        api.wallet['USD']['Free'] = 101100
        engine.step(quotes(), NOW+30000)
        result = snapshot(self.store.db, 'paper-exchange', NOW+30000)
        self.assertEqual(result['activity']['days_with_confirmed_filled_orders'], 0)
        self.assertTrue(result['activity']['persistent_halt_before_eight_recorded_days'])
        self.assertFalse(result['activity']['organizer_minimum_trades_per_day_verified'])

    def test_trading_day_report_counts_actual_fills_and_isolates_mode(self):
        for n, offset in enumerate((0, 1000, 86400000)):
            self.store.db.execute('INSERT INTO fills VALUES(?,?,?)', (str(n), NOW+offset, json.dumps({'mode': 'paper-exchange', 'quantity': 10, 'side': 'BUY', 'pair': 'PEPE/USD'})))
        self.store.db.execute('INSERT INTO fills VALUES(?,?,?)', ('shadow', NOW+2*86400000, json.dumps({'mode': 'shadow', 'quantity': 10})))
        self.store.db.commit()
        result = snapshot(self.store.db, 'paper-exchange', NOW+2*86400000)
        self.assertEqual(result['activity']['days_with_confirmed_filled_orders'], 2)
        self.assertEqual(sum(result['activity']['filled_orders_by_hkt_day'].values()), 3)
        self.assertEqual(snapshot(self.store.db, 'shadow', NOW+2*86400000)['activity']['days_with_confirmed_filled_orders'], 1)


if __name__ == '__main__': unittest.main()
