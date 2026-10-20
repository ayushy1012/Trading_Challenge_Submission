# Execution findings and the updated trading candidate

Updated 10 October 2026. **The execution adapter works after the fixes below.
A reliably profitable strategy has not been established.** The additional
research does not support promising a 1% competition return. The package keeps
the capital-allocation gate closed until actual completed cycles support it.

## What the authenticated tests actually establish

These tests used the supplied near-$50,000 **test account**, not the competition
portfolio. All writes came from bounded autonomous programs with durable order
intents. The final signed audit found $49,999.87 free USD, no locked USD, no
nonzero coin holdings, no pending orders and no shorts. No bot is left running
on that account by this investigation.

| Order | Action | Price | Requested quantity | Result | Fee |
| --- | --- | ---: | ---: | --- | ---: |
| 3450952 | Resting LIMIT BUY PEPE | 0.00000402 | 24,875,621 | PENDING, then CANCELED unfilled | Zero charged |
| 3450990 | Marketable LIMIT BUY PEPE | 0.00000403 | 4,962,000 | FILLED, TAKER | 0.1%, $0.019996 |
| 3450997 | Marketable LIMIT SELL PEPE | 0.00000402 | 4,962,000 | FILLED, TAKER | 0.1%, $0.019947 |

The resting-order session had a 15-minute budget, including its five-minute
warmup. Its entry was automatically canceled and reconciled at the deadline.
Its cash returned to $49,999.96. The separate marketable-limit classification
test deliberately paid the spread on a position below $20; measured cash
decreased by $0.09. It tested execution, **not profitability**.

At several signed observations, the resting order remained unfilled with
`LastPrice = limit = 0.00000402`, bid 0.00000402 and ask 0.00000403. Thus a
last-price touch is **not sufficient** to infer a fill. This observation does
not identify every internal matching rule or exclude intervening quotes.

Crossing a quote with a LIMIT order is also not a maker-fee shortcut: both
marketable LIMITs immediately filled as TAKER at 0.1%. The bot checks actual
`Role`, commission and balances instead of treating every LIMIT as a maker.

Evidence is in `research/execution_validation/`: final signed responses, raw
pending-order data, both execution logs, reports and `actual_test_trades.csv`.
CSV observation times and exchange create/finish times are separate. Synthetic
unit-test logs are labeled separately and are not account trades. The probe
journals are excluded from the deployable package so they cannot be mistaken
for your competition ledger.

## Four execution corrections

1. The current endpoint returns `SpotWallet` and `MarginWallet`, whereas the
   older specification documents `Wallet`. The adapter supports both spot
   shapes, validates amounts and USD, and rejects a missing wallet. It never
   silently replaces a malformed account with zero cash. The tick strategy
   rejects nonzero margin funds it does not manage.
2. The first order-history page omits `offset`; subsequent pages retain
   positive offsets. This addresses the reported live `offset=0` rejection.
   A query by order ID sends that ID alone among optional parameters. Missing
   order or short-position lists are errors rather than an empty account.
3. Live order queries echo the requested quantity in `FilledQuantity` while
   average price, transfers and commission remain zero. On order 3450952,
   USD Free plus Lock was one cent below the pre-order total: Free and Lock
   are displayed independently at cent precision. The old 0.000001-dollar
   reconciliation check rejected this pending response indefinitely, preventing
   normal expiry management. Normalization now requires the full-size echo,
   zero execution fields, an unchanged base holding and a USD difference within
   four cents. It records zero execution; it never manufactures a fill.
4. Windows lock failures now close the handle and produce the same clear
   concurrent-instance error as Linux. Either `ROOSTOO_SECRET_KEY` or
   `ROOSTOO_API_SECRET` can supply the private secret.

131 regression tests pass. They cover schema failures, the cent-rounding echo,
unknown-write recovery, cancellation/fill races, session cleanup, risk latches,
history changes, quote-quality gates and conservative P&L evidence. The Windows
branch was tested with a Windows-lock fixture, not on a native Windows host.

## What the additional strategy research found

`research/compare_paper_edges.py` tested nine price-grid policies across PEPE,
SHIB and BONK and three execution models: **81 development runs**. The policies
varied pullback depth, target ticks, stop distance, patience and quiet-market
filters. `research/compare_reversion_edges.py` added twelve PEPE combinations
using mean discounts, floor bounces and stationary range floors: **36 runs**.
None of the 39 policy/coin combinations profited in every execution model.

Three additional October 9 Binance spot minute archives were downloaded and
checked against their published SHA-256 checksums. They add 4,320 genuinely new
minute rows to the previous research period, and a separate **81 one-day
checks**. They are USDT price proxies, not Roostoo order books. One day is too
short to establish an edge. October 9 was not used to define the initial grid,
but all historical development data had already been inspected, so selection
bias remains. The later reversion study reused history and has no new holdout.

Examples for PEPE from the simplified historical simulator:

| Policy and period | Touch fills | Sampled-close fills | One-extra-tick trade-through |
| --- | ---: | ---: | ---: |
| Buy at current last, Sep 1–Oct 8 | −2.4487% | −2.4820% | −2.5263% |
| One-tick pullback, Sep 1–Oct 8 | +14.4508% | +2.0293% | −2.5100% |
| Quiet one-tick pullback, Sep 1–Oct 8 | +14.8231% | +2.8336% | −2.5581% |
| Quiet one-tick pullback, Oct 9 only | +0.4812% | +0.4794% | −0.7687% |

These figures use the simulator's original full-size allocation and loss
governor. They do **not** include the current small-order verification stage,
profit locks, actual bid/ask paths, latency or partial fills. The current code's
quote-history filters also differ from minute high/low filters. The numbers
are conditional model comparisons, not a forecast of the updated bot's return.

The separate replay feeds recorded Roostoo snapshots into the actual engine.
It includes 62 real snapshots over about 33 minutes. A last-touch shadow model
invented two profitable cycles and about $0.30 profit; the executable-quote
version produced zero fills, consistent with the resting-order test. The new
primary policy skipped frozen executable quotes after warming up. A final
150-second data gap correctly reset entry warmup. The exploratory watchlist
placed one simulated WLFI entry and recorded no completed cycle. Every replay
fill is labeled **shadow**, and none qualifies the account for larger trades.

## The strongest candidate to investigate

The default `tick_config.json` now implements a **quiet-market pullback cycle**
on PEPE. Its motivation is buying temporarily cheaper inventory and selling a
rebound on a relatively coarse price grid. Cheap fees and full hypothetical
fills alone do not create positive expectancy.

* Accumulate thirty minutes of actual quotes, resetting entry warmup after a
  data gap. Last-price flicker with an unchanged bid and ask does not qualify.
* Require sufficient volume, a spread of at most two ticks, limited recent
  movement and a conditional target return of at least six basis points after
  maker fees. Retain the falling-price guard.
* Buy at the lowest of the configured bid-based price, one tick below the ask,
  and one tick below the current rounded last. This avoids chasing the current
  last when it equals the bid. It may reduce fills; a missing cheap entry is
  preferable to assuming a favorable fill.
* After an independently confirmed buy, submit a sequential fee-aware sell.
  The target is at least one tick above the actual fill and covers measured
  entry cost. Keep one position and one resting order. Never average down.
* Keep ten-minute entry expiry, thirty-minute inventory expiry, a three-tick
  price stop, cancellation reconciliation and emergency market exits.
* Limit each initial trade to $100. Larger trades require at least twenty
  complete **current-policy, current-account-mode** cycles, positive conservative
  net mean, a positive block-bootstrap lower mean and a loss-aware win-rate
  check. Subtract four cents of account-display uncertainty per closed paper
  cycle for validation; raw wallet P&L remains visible. Older policy samples
  cannot establish the new policy's performance.

`tick_config_watchlist.json` is an **optional, unvalidated** extension to PEPE,
SHIB and WLFI. It rotates one position among eligible moving quotes; it needs
separate actual evidence for each pair. The main configuration remains PEPE
only. BONK, despite a larger tick gain in the snapshot, was below the existing
$2 million volume floor. Fine-tick BTC/BNB-style one-tick trades fail the fee
test. Tokenized stocks were not added without relevant evidence.

## Economics and loss/profit controls

At entry 0.00000402 and target 0.00000403, a completed maker/maker cycle yields
about **0.14853%**, allowing for either supported entry-fee convention. A
three-tick stop plus one-tick exit allowance models a **1.14502%** loss. Raw
break-even win rate is about **88.52%**. Eighty-percent winners still lose under
that payoff pattern. Accounting uncertainty makes the live scaling test more
conservative. These are conditional payoffs, not estimated probabilities.

The risk engine retains +1.10% total profit, +0.35% daily profit, −0.75% daily
loss, −2.50% total loss, −2.50% peak drawdown and three consecutive daily losses.
It also halts if measured conservative net expectancy is nonpositive after
twenty cycles or remains unverified after sixty. Daily resets use Hong Kong
time; persistent halts survive restart. It cancels, confirms terminal status,
reconciles and exits owned inventory before ceasing new trades. Gaps, outages
and delayed execution can exceed a client-side loss budget.

The competition's eight-active-day requirement still matters: a persistent
profit lock reached early can conflict with it. Reports flag this; the bot
does not manufacture activity trades. A baseline recovered from your preserved
ledger is not necessarily the organizer's original funding value.

## Run and update

Preserve your private environment, Git history and existing `runtime/` ledger for the same account. The account binding checks owned order identity when upgrading or rotating keys; it prevents a test account baseline or evidence from being reused for competition funds. Configure a separate runtime directory when changing accounts, and retain the old ledger.
Update code while the old process is stopped, and run only one bot per account.
Do not copy these test reports into your runtime state.

```bash
python -m unittest discover -s tests -q
python -m sentinel preflight --authenticated
python -m sentinel tick-status --mode paper-exchange --remote --output runtime/diagnostics
python -m sentinel tick-run --mode paper-exchange --max-minutes 120 --max-entries 3
```

The bounded command allows time for the new thirty-minute warmup. It stops
opening entries at its budget and keeps reconciling/closing owned exposure;
an outage can extend cleanup beyond the time limit. A later explicitly invoked
run may resume a completely cleaned-up bounded session without clearing any
risk latch. To run continuously, omit the two budget arguments. Reports update
about every minute and remain local.

The standalone `research/probe_marketable_limits.py` is a cost-incurring **test
account classification experiment**, not the trading strategy. It refuses a
$100,000 competition wallet and a funded/active account handover. Its results
are kept separate from strategy-validation samples. Do not rerun it merely to
produce activity.

## Primary sources and implementation inspiration

* [Official Roostoo API](https://github.com/roostoo/Roostoo-API-Documents): request
  signing, precision, order-ID queries, cancellation and reported roles.
  Only documented order parameters are sent. SpotWallet and pending rounding
  corrections come from the actual signed observations above. No server-side
  stop, post-only or guaranteed maker facility is assumed.
* [Competition event](https://luma.com/coghwiyt): fees, timeframe and activity
  constraints, together with the user's specific clarification permitting
  sequential tick scalping.
* [Original inspiration](https://github.com/danielchancfa/web3-quant-hackathon):
  application architecture and API workflow; no return claim was imported.
* [HftBacktest](https://github.com/nkaz001/hftbacktest) and its
  [fill-model documentation](https://hftbacktest.readthedocs.io/en/latest/order_fill.html):
  distinguish price touches, executable quotes, queues, partial fills and market
  impact. This motivated execution stress, not an HFT deployment.
* [Hummingbot's controller](https://github.com/hummingbot/hummingbot/blob/master/controllers/generic/pmm_v1.py):
  inventory awareness and controlled order refresh. The implemented bot remains
  sequential; its simultaneous quoting strategy was not copied.
* [Freqtrade lookahead analysis](https://www.freqtrade.io/en/stable/lookahead-analysis/):
  causal features and skepticism about favorable backtests. Freqtrade's own CLI
  was not run against this custom engine.
* [Explainable Patterns in Cryptocurrency Microstructure, 2026](https://arxiv.org/html/2602.00776v1):
  order-flow/depth features and severe maker adverse selection during crashes.
  Roostoo ticker data does not supply those depth features, so the paper's
  model or reported profits were not transplanted into this bot.
* [Binance public data](https://github.com/binance/binance-public-data): archive
  format, microsecond timestamps and checksum verification.

The paper-exchange opportunity worth testing is coarse-grid, patient execution
with strictly measured net results. The two attractive shortcuts tested here—
last-touch fills and maker fees on crossing LIMITs—did not hold up.
