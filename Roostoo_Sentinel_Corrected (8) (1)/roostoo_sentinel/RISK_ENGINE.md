# Profit stops, loss stops and profitability checks

**Latest validation:** [PAPER_EDGE_RESEARCH.md](PAPER_EDGE_RESEARCH.md) adds signed
test-account results and 131 regression tests. Current paper-cycle validation
subtracts a four-cent wallet-display allowance and uses evidence version 3.
The stops below remain unchanged. The 98-test count and overlay in this report
describe the earlier risk-engine release, not newly measured trading profits.

These settings apply to `python -m sentinel tick-run`. The separate original
hourly runner uses its own configuration and risk module.

## Default stops

| Control in `tick_config.json` | Default | Example with a $100,000 reference | Resume behavior |
|---|---:|---:|---|
| `total_profit_target` | +1.10% | Halt at $101,100 | Persistent |
| `daily_profit_target` | +0.35% | Halt at $100,350 if the day starts at $100,000 | Next HKT day |
| `daily_loss` | −0.75% | Halt at $99,250 if the day starts at $100,000 | Next HKT day |
| `max_total_loss` | −2.50% | Halt at $97,500 | Persistent |
| `max_drawdown` | −2.50% from recorded equity peak | $97,500 if the peak is $100,000 | Persistent |
| `max_consecutive_losses` | 3 completed losing cycles in the HKT day | Independent of loss amount | Next HKT day |

Equality triggers a stop. A daily reset permits new entries only if no persistent
halt applies. HKT midnight is 21:30 IST on the preceding Indian calendar date.
Daily reference equity is the first risk mark observed that HKT day. Total
reference equity is the earliest recorded portfolio mark in the existing mode's
ledger, or first observed equity for a new ledger. Restarting or upgrading
preserves this reference. It does not reconstruct funding before the bot first
recorded the account. Use a dedicated account without deposits or withdrawals.

Stops use **conservative liquidation equity**: free plus locked USD, and all
spot inventory valued at the bid less `exit_slippage_ticks` (one tick by default)
and the taker sell fee. Last prices and pending limit prices are not guaranteed
liquidation value. The 0.10 percentage point gap above a 1% benchmark is a buffer;
it does not guarantee a final realized return of 1.10%.

When a stop triggers the bot persists the halt before any order change, cancels
its owned pending entry or exit, and waits for terminal status and reconciled
balances. It then market-sells remaining managed inventory. A cancel
acknowledgement alone never permits another sell. A target filled during
cancellation does not cause a duplicate sell. The process continues monitoring
and reports while refusing new buys. Persistent halts survive restarts,
configuration changes and new days.

The three-tick position stop, 30-minute holding limit and competition cutoff
remain active. Planned stop risk is capped at 0.15% of equity and allocation at
15%. These are sizing assumptions, not maximum-loss guarantees. The runner
checks about every 30 seconds; gaps, API outages, unresolved writes and late
cancellations can delay or worsen exits. These are client-side controls, not
exchange-native stop orders. Stopping the process does not flatten inventory.

## Eligibility for larger trades

Until verification passes, each entry is capped at **$100**, further reduced by
available cash and the normal risk budget. Only complete cycles from the current
mode, configuration and evidence convention qualify. Shadow observations never
qualify a paper-exchange account. Positions from older configurations are still
managed; their exits do not validate a new policy.

At least 20 complete cycles are required, using the last 60 if available. All
these checks must pass:

- Actual net cycle returns are positive on average after account fees.
- The one-sided 90% lower mean from a circular five-cycle block bootstrap is
  positive, allowing for short clusters of losses.
- Observed win rate exceeds both `probe_min_win_rate` and economic breakeven
  plus `probe_win_rate_margin` (two percentage points by default).
- The one-sided 90% Wilson lower win-rate bound exceeds economic breakeven.

Breakeven is `loss / (win + loss)`. Win allowance is the lesser of the modeled
fee-adjusted target and average observed positive cycle. Loss allowance is the
greater of the modeled stop/slippage/fee loss and average observed negative
cycle. Sizing also allows for the worst observed loss in the window. A no-loss
sample therefore does not imply that losses cost zero.

At an illustrative PEPE entry of 0.00000388 and a tick of 0.00000001, with 0.05%
maker and 0.10% taker fees, a successful cycle nets approximately **0.1575%** of
entry cost. A three-tick stop plus one-tick allowance costs about **1.1809%**.
Breakeven is roughly **88.23% winners**. An 80% win rate can lose money. These
figures are conditional on the assumed prices and fees, not expected returns.

Partial exits are combined into one cycle. Unsellable dust cost is conservatively
written off in validation P&L. Order logs retain the actual allocated realized
P&L, and wallet dust remains in portfolio equity. Unfilled cancellations are
never wins. Old evidence conventions cannot automatically enable larger trades.

With at least 20 cycles, a nonpositive rolling net mean persistently halts the
candidate with `profitability_check_failed`. Failure to verify after 60 completed
cycles (`probe_max_cycles`) also halts it. Three consecutive losing cycles can
stop the day earlier. Review the account and evidence before any manual
intervention; preserve the ledger rather than deleting it to bypass controls.
These descriptive checks are repeatedly monitored and depend on the observed
regime; they are not formal confidence guarantees for future returns.

## Evidence and validation

The original candle simulations remain fill-sensitive. Across 19 independent
14-day episodes, their mean return was +5.807% with touch fills and −2.511% with
trade-through fills. The corrected best-bid bot has no authenticated account
profitability result in this package. No private trade or deployment occurred
while adding this risk engine.

`python research/validate_risk_policy.py` applies a **conditional risk overlay**
to the archived September–8 October simulated trade streams. It resizes old
entry/exit outcomes and applies the new admission and stop rules. It does not
regenerate entries, model intratrade equity, or reproduce executable matching.
It is not an execution backtest of the corrected best-bid policy.

| Archived fill stream | Conditional cash return | Accepted cycles | Larger cycles | Result |
|---|---:|---:|---:|---|
| Touch | +1.10687% | 88 | 63 | Halt at total profit target |
| Sampled close | −0.000347% | 29 | 0 | Reject profitability and halt |
| Trade-through | −0.011562% | 20 | 0 | Reject profitability and halt |

Source hashes, assumptions and results are in `research/risk_overlay.json`.
These are selected simulated streams inherited from earlier exploratory work;
the replay is neither measured Roostoo profit nor an untouched holdout. Some
source streams ended early after the earlier simulator halted. Skipping trades
can change real future signals; this overlay cannot reproduce that effect.

All 98 regression tests passed. They cover threshold equality, durable halts, HKT resets, baseline
migration, mode isolation, fees, loss clusters, partial-cycle accounting,
profitability rejection and cancellation/fill races. Synthetic fixtures verify
behavior, not an economic edge.

## Running and monitoring

The official event also requires at least eight active competition trading days
with enough strategy trades each day: <https://luma.com/coghwiyt>. A persistent
profit halt reached earlier can conflict with that requirement. Reports expose
recorded HKT days with confirmed fills, order and cycle counts by day, and a
warning if a persistent halt occurs before eight recorded days. These counts
cover the entire recorded mode ledger. They do not certify dates outside the
competition window, activity missing from an earlier ledger, or what qualifies
as "enough" daily trades. Risk stops remain active; the bot does not create
trades solely to satisfy a day count.

Stop the earlier process before merging updated source. Preserve `.env`, Git
history and `runtime/tick_paper_exchange.sqlite3`. Use one process per account.

```bash
python -m unittest discover -s tests -v
python -m sentinel tick-status --mode paper-exchange --remote --output runtime/diagnostics
python -m sentinel tick-run --mode paper-exchange
```

`runtime/reports/paper-exchange/summary.txt` and `status.json` expose thresholds,
recorded returns, halt reasons and profitability checks. `trade_logs.csv`
contains confirmed executed orders. Reports update approximately every minute.
Read-only diagnosis does not submit or cancel. New evidence conventions require
new small probes; do not reset the existing account ledger to obtain them.

Roostoo's documented spot order, cancellation, balance and commission fields
remain unchanged: <https://github.com/roostoo/Roostoo-API-Documents>. The risk
engine wraps those calls locally; it sends no unsupported stop/profit fields.
