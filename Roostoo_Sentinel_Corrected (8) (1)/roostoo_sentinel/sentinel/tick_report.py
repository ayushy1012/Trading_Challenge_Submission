"""Read-only account diagnosis and exports from the existing tick ledger.

No function in this module submits, cancels, or modifies an exchange order.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sqlite3
import time

from .api import ApiError, Roostoo

HKT = timezone(timedelta(hours=8))
IST = timezone(timedelta(hours=5, minutes=30))


def iso(milliseconds, zone=timezone.utc):
    return datetime.fromtimestamp(int(milliseconds) / 1000, zone).isoformat(timespec='seconds')


def get(db, key, default=None):
    row = db.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def fills(db, mode):
    result = []
    for identity, timestamp, payload in db.execute('SELECT id,timestamp_ms,payload FROM fills ORDER BY timestamp_ms,id'):
        row = json.loads(payload)
        if row.get('mode', mode) == mode and float(row.get('quantity', 0)) > 0:
            result.append({'id': identity, 'timestamp_ms': timestamp, **row})
    return result


def daily_counts(db, now, mode):
    start = datetime.fromtimestamp(now / 1000, HKT).replace(hour=0, minute=0, second=0, microsecond=0)
    lower, upper = int(start.timestamp()*1000), int((start+timedelta(days=1)).timestamp()*1000)
    attempts, accepted, rejected, unfilled = 0, 0, 0, 0
    for status, payload, response in db.execute('SELECT status,payload,response FROM intents WHERE created_ms>=? AND created_ms<?', (lower, upper)):
        row = json.loads(payload)
        if row.get('mode', mode) != mode or row.get('side') != 'BUY':
            continue
        attempts += 1
        rejected += status == 'rejected'
        accepted += status in {'acknowledged', 'resting', 'done', 'canceled'}
        detail = (json.loads(response) if response else {}).get('OrderDetail', {})
        unfilled += detail.get('Status') in {'CANCELED', 'CANCELLED', 'REJECTED'} and float(detail.get('FilledQuantity', 0) or 0) == 0
    today_fills = [r for r in fills(db, mode) if lower <= r['timestamp_ms'] < upper]
    samples = get(db, 'tick_closed_samples', [])
    completed = sum(lower <= r['timestamp_ms'] < upper and r.get('mode', mode) == mode and r.get('cycle_closed', True) for r in samples)
    return {'hkt_day': start.date().isoformat(), 'entry_attempts_today': attempts,
            'accepted_entries_today': accepted, 'rejected_entries_today': rejected,
            'unfilled_terminal_entries_today': unfilled,
            'filled_entries_today': sum(r.get('side') == 'BUY' for r in today_fills),
            'filled_orders_today': len(today_fills), 'completed_cycles_today': completed}


def snapshot(db, mode, now):
    errors = []
    for ts, kind, payload in db.execute("SELECT timestamp_ms,kind,payload FROM events WHERE kind IN ('tick_api_pause','tick_order_rejected','tick_write_unknown','tick_cancel_rejected','tick_paused') ORDER BY id DESC LIMIT 5"):
        errors.append({'timestamp_ms': ts, 'event': kind, **json.loads(payload)})
    rows = fills(db, mode)
    samples = [r for r in get(db, 'tick_closed_samples', []) if r.get('mode', mode) == mode]
    risk = get(db, 'tick_risk_status', {})
    if risk.get('mode', mode) != mode: risk = {}
    evidence = {pair: value for pair, value in get(db, 'tick_profitability', {}).items() if value.get('mode', mode) == mode}
    filled_by_day, cycles_by_day = {}, {}
    for row in rows:
        day = iso(row['timestamp_ms'], HKT)[:10]
        filled_by_day[day] = filled_by_day.get(day, 0)+1
    for row in samples:
        if row.get('cycle_closed', True):
            day = iso(row['timestamp_ms'], HKT)[:10]
            cycles_by_day[day] = cycles_by_day.get(day, 0)+1
    activity = {'scope': 'entire_recorded_mode_ledger',
                'days_with_confirmed_filled_orders': len(filled_by_day),
                'filled_orders_by_hkt_day': filled_by_day, 'completed_cycles_by_hkt_day': cycles_by_day,
                'competition_required_active_days': 8,
                'organizer_minimum_trades_per_day_verified': False,
                'qualification_claim': 'Unknown: recorded days alone do not establish enough strategy trades each competition day.',
                'persistent_halt_before_eight_recorded_days': len(filled_by_day) < 8 and any(reason in risk.get('halt_reasons', []) for reason in ('total_profit_target', 'total_loss_limit', 'drawdown_limit', 'profitability_check_failed'))}
    return {'mode': mode, 'execution_source': 'signed_roostoo_account' if mode == 'paper-exchange' else 'local_shadow_simulation',
            'generated_at_utc': iso(now), 'generated_at_ist': iso(now, IST),
            'state': get(db, 'tick_state', {}), 'portfolio': get(db, 'tick_last_snapshot', {}),
            'last_decision': get(db, 'tick_last_decision', {}), 'daily_counts': daily_counts(db, now, mode),
            'risk': risk, 'profitability': evidence,
            'activity': activity,
            'total_recorded_filled_orders': len(rows),
            'total_recorded_completed_cycles': sum(r.get('cycle_closed', True) for r in samples),
            'recorded_realized_pnl_usd': sum(float(r['pnl']) for r in samples),
            'recent_errors': errors, 'fee_violation': get(db, 'tick_fee_violation'),
            'bounded_session':get(db,'tick_bounded_session'),'session_stop':get(db,'tick_session_stop'),
            'unresolved_writes': [{'intent': identity, 'status': status, 'payload': json.loads(payload)} for identity, status, payload in db.execute("SELECT id,status,payload FROM intents WHERE status IN ('submitted','acknowledged','unknown') ORDER BY created_ms")]}


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(text, encoding='utf-8')
    temp.replace(path)


def export_report(db, mode, destination, now):
    dest = Path(destination)
    report = snapshot(db, mode, now)
    lines = ['Roostoo tick bot summary', f"Execution source: {report['execution_source']}",
             f"Generated UTC: {report['generated_at_utc']}", f"Generated IST: {report['generated_at_ist']}",
             f"Phase: {report['state'].get('phase', 'not_started')}",
             f"Pair: {report['state'].get('pair', '')}",
             f"Last portfolio equity USD: {report['portfolio'].get('equity', 'unavailable')}",
             f"Conservative liquidation equity USD: {report['risk'].get('liquidation_equity', 'unavailable')}",
             f"Risk halt reasons: {', '.join(report['risk'].get('halt_reasons', [])) or 'none recorded'}",
             f"Total return from recorded baseline: {report['risk'].get('total_return', 'unavailable')}",
             f"Daily return (HKT): {report['risk'].get('daily_return', 'unavailable')}",
             f"Last decision: {report['last_decision'].get('reason', 'unavailable')}"]
    lines.extend(f'{name}: {value}' for name, value in report['daily_counts'].items())
    for pair, evidence in report['profitability'].items():
        lines.append(f"{pair}: cycles for current policy={evidence['completed_cycles_for_policy']}, larger trades permitted={evidence['verified_for_scaling']}, net mean={evidence['mean_net_cycle_return']}, block lower mean={evidence['mean_return_block_lower_90pct']}, win rate={evidence['win_rate']}, breakeven win rate={evidence['breakeven_win_rate']}")
    lines.extend([f"Total recorded filled orders: {report['total_recorded_filled_orders']}",
                  f"Total recorded completed cycles: {report['total_recorded_completed_cycles']}",
                  f"Recorded realized P&L USD: {report['recorded_realized_pnl_usd']:.8f}",
                  f"Unresolved writes: {len(report['unresolved_writes'])}",
                  f"Fee violation: {report['fee_violation']}",
                  f"Recorded HKT days with confirmed filled orders: {report['activity']['days_with_confirmed_filled_orders']}",
                  'Competition requires at least 8 active days with enough strategy trades each day. Recorded day count alone does not verify qualification.',
                  f"Persistent halt before 8 recorded trading days: {report['activity']['persistent_halt_before_eight_recorded_days']}",
                  'Submitted or canceled unfilled orders are not completed trades.',
                  'These files are local exports. No email, Telegram or external delivery is configured.'])
    atomic_write(dest/'summary.txt', '\n'.join(lines)+'\n')
    atomic_write(dest/'status.json', json.dumps(report, indent=2, allow_nan=False)+'\n')
    fields = ['timestamp_utc', 'timestamp_ist', 'timestamp_ms', 'order_id', 'pair', 'side', 'order_type', 'quantity', 'price', 'limit_price', 'fee_rate', 'commission_coin', 'commission_amount', 'role', 'status', 'exchange_create_timestamp_ms', 'exchange_finish_timestamp_ms', 'pnl_usd', 'mode', 'config_hash', 'commit']
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    for row in fills(db, mode):
        writer.writerow({**row, 'order_id': row.get('order_id', row['id']), 'timestamp_utc': iso(row['timestamp_ms']), 'timestamp_ist': iso(row['timestamp_ms'], IST)})
    atomic_write(dest/'trade_logs.csv', stream.getvalue())
    return report


def diagnose(config='tick_config.json', mode='paper-exchange', remote=False, output=None):
    from .tick import TickConfig
    c = TickConfig.load(config)
    now = int(time.time()*1000)
    remote_data = None
    if remote:
        if mode != 'paper-exchange':
            raise ValueError('Remote diagnosis belongs to paper-exchange mode')
        api = Roostoo(spacing=2.)
        if not api.authenticated:
            raise ValueError('Private Roostoo credentials are required for read-only account diagnosis')
        api.sync_time()
        remote_data = {'writes_performed': 0, 'read_errors': {}}
        def read(name, call):
            try:
                return call()
            except ApiError as error:
                remote_data['read_errors'][name] = str(error)
                return None
        info = read('exchange_info', api.exchange_info) or {}
        ticker = read('ticker', api.ticker) or {}
        remote_data.update({'wallet': read('balance', api.balance),
                            'pending_orders': read('pending_orders', lambda: api.query_orders(pending_only=True)),
                            'recent_orders': read('recent_orders', lambda: api.query_orders(limit=100)),
                            'short_positions': read('short_positions', api.short_positions),
                            'exchange_running': info.get('IsRunning'),
                            'pairs': {p: {'metadata': info.get('TradePairs', {}).get(p), 'quote': ticker.get('Data', {}).get(p)} for p in c.pairs}})
        remote_data['wallet_schema']=getattr(api,'last_balance_accounts',{}).get('schema')
        remote_data['margin_wallet']=getattr(api,'last_balance_accounts',{}).get('margin')
        now = api.now_ms()
        remote_data['server_time_utc'] = iso(now)
    path = Path(c.runtime_dir)/('tick_'+mode.replace('-', '_')+'.sqlite3')
    report = {'mode': mode, 'ledger_exists': path.exists(), 'ledger_path': str(path)}
    if path.exists():
        db = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)
        try:
            db.execute('BEGIN')
            report.update(export_report(db, mode, output, now) if output else snapshot(db, mode, now))
        finally:
            db.close()
    if remote_data is not None:
        report['remote'] = remote_data
        if output:
            atomic_write(Path(output)/'account_diagnosis.json', json.dumps(report, indent=2, allow_nan=False)+'\n')
    return report
