# Roostoo Sentinel — Sigma Gate + LightGBM

**An implemented, reproducible two-model allocation strategy, with execution costs and a complete audit trail.**

Sigma Gate identifies persistent crypto trends and controls portfolio risk. A five-seed LightGBM ensemble adjusts exposure according to agreement with that trend. This update replaces the draft's proposed confidence rules with working code, tests, trained models, trade logs and measured comparisons.

The frozen hybrid earned **+6.06% net in the 2026 evaluation**, with **5.90% maximum drawdown** and **0.91 daily-return Sharpe**. Its 2025 selection-year return was **+6.10%**, with **3.98% drawdown**. It stayed positive in every planned execution stress case.

**The stronger core strategy remains Sigma alone:** it earned **+9.58%** in the same 2026 period, with **5.71% drawdown**. The hybrid improved validation drawdown but did not establish an incremental advantage in 2026. The core configuration therefore stays unchanged; the hybrid is a separate, selectable research candidate.

All numbers are **historical simulations after costs**, starting with $100,000. They are not live account returns. The 2026 data had already appeared in upstream and earlier Sigma research; it is a later chronological evaluation, **not a globally untouched holdout**. Research completed 20 October 2026.

## Results a reviewer can reproduce

Calendar 2025 is the policy-selection year. The later evaluation runs from the 1 January 2026 decision through terminal liquidation at **9 October 2026 23:00 UTC**. Both periods reset cash and portfolio state. The first 30 days of 2025 establish out-of-training prediction ranks before hybrid entries.

| Strategy | 2025 net return | 2025 max drawdown | 2026 net return | 2026 daily Sharpe | 2026 max drawdown |
|---|---:|---:|---:|---:|---:|
| **Sigma core (stronger 2026 baseline)** | +10.00% | 6.88% | +9.58% | 1.06 | 5.71% |
| **Frozen Sigma + LightGBM hybrid** | +6.10% | 3.98% | +6.06% | 0.91 | 5.90% |
| LightGBM only, 4h adaptation | -4.96% | 8.63% | -10.25% | -2.40 | 10.63% |
| LightGBM only, 24h adaptation | -1.68% | 5.51% | -7.34% | -1.59 | 8.73% |
| LightGBM only, 72h adaptation | +4.00% | 3.72% | -1.76% | -0.35 | 6.44% |
| 95% equal-weight buy and hold | -7.71% | 44.82% | -13.97% | -0.15 | 47.27% |
| Cash, no yield | +0.00% | 0.00% | +0.00% | — | 0.00% |

The ML-only rows use the same hourly features, portfolio accounting and trading costs, with a top-5% absolute prediction trigger. They are **adaptations**, not a reproduction of the original minute PEPE backtest. The equal-weight benchmark buys the same five assets once, pays entry and final exit costs, and holds approximately 95% gross exposure without the strategy's volatility governor.

Sharpe uses UTC daily net NAV returns, sample standard deviation, zero cash yield and a √365 annualization factor. Drawdown uses hourly net liquidation NAV. The raw summaries also retain hourly Sharpe; do not mix the two definitions. Returns in the table are period returns, not annualized returns.

<!-- ![Measured returns, drawdowns, monthly results and execution stress](research/hybrid/figures/hybrid_results_2026.png) -->

### 2026 hybrid trade and capital statistics

| Measure | Measured value |
|---|---:|
| Net P&L / final cash | $6,062.93 / $106,062.93 |
| Daily Sortino / annualized Calmar | 2.04 / 1.34 |
| Simulated fills / closed position episodes | 606 / 134 |
| Profitable / unprofitable episodes | 31 / 103 |
| Net episode win rate / profit factor | 23.13% / 1.64 |
| Median episode duration | 148 hours |
| Commission / two-way notional turnover ÷ starting NAV | $2,212.20 / 22.12× |
| Average gross exposure | 12.74% |
| Profitable calendar months | 5/10 (October partial) |

A position episode starts from flat and ends at flat; allocation adjustments are fills inside that episode. Win rate includes fees. The cash-flow sum of all episodes reconciles to final NAV. This is a trend-following payoff profile with infrequent larger winners, rather than a high-win-rate scalper.

## How the two scores combine

### 1. Structural score: Sigma Gate

The fixed universe is **BTC/USD, ETH/USD, SOL/USD, BNB/USD and XRP/USD**. It is inherited from the supplied Sigma study; instruments were not selected for their winning 2026 returns.

For each completed hourly candle, three hysteresis gates compare the log-price move over **126, 252 and 540 hours** with its volatility-scaled threshold:

```text
z(L) = [log(close[t]) − log(close[t−L])] / [EWMA_hourly_vol[t] × sqrt(L)]
gate(L) = sign(z(L)) when |z(L)| > 0.5; otherwise retain the previous gate
S = mean(gate(126), gate(252), gate(540))
```

The sign of `S` sets direction. Its magnitude measures agreement between trend horizons; with three initialized ±1 gates, values are ±1 and ±⅓. A score of 1 means all three gates agree. It is **not a 100% probability of profit**.

The existing risk engine then uses inverse-volatility allocation, a 20% annual volatility **sizing target**, a fast-volatility shock guard, a rolling drawdown governor and exposure ceilings. The sizing target is not a guaranteed realized volatility: weak gates, ML reductions and cash lower actual exposure.

### 2. Forecast and strength score: LightGBM

The supplied PEPE script is a minute-data research model. Its required `train_pepe.csv`, `pepe_2025_val.csv` and `pepe_2026_test.csv` were not attached. This implementation retains its causal technical features, volatility-normalized labels, fixed seed ensemble and trailing thresholds, adapted to the supplied hourly OHLCV histories. Missing trade-count and taker-flow data are omitted; they are not synthesized.

The selected model predicts the **72-hour forward log return normalized by trailing volatility**, from the next candle open to the open 72 hours later. That horizon was selected from 4, 24 and 72 hours using 2025 validation. It is a forecast horizon, not a forced 72-hour exit.

- **76 features:** normalized returns, EMA gaps, relative volatility, range location, RSI, ATR, relative volume and path efficiency, plus 1/4/24-hour lags.
- **Training:** 2022–2024, pooled across the five assets, every third hour per asset; **42,955** valid observations for the selected 72h model. Every training label finishes strictly before 1 January 2025. No price level, asset identifier or future target enters the features.
- **Five fixed seeds:** 42, 7, 123, 2024 and 99; predictions are averaged.
- **Fixed architecture:** Huber loss, alpha 0.9, 120 trees, depth 4, 15 leaves, learning rate 0.03, minimum leaf 250, L1 1 and L2 10. Normalized training targets are clipped to ±5. No early stopping or seed selection is claimed.

Let `m` be the average model forecast. Its strength score `R` is the fraction of the **previous 720 hourly absolute predictions** that are no larger than `|m|`. Only predictions made after training enter this history. `R = 0.75` means the forecast is stronger than 75% of recent model outputs; it does **not** mean a 75% chance of a winning trade.

### 3. Selected combination: agreement-weighted exposure

| Sigma and ML state | Multiplier on the structural score |
|---|---:|
| Same direction and `R ≥ 0.50` | 1.00× |
| Same direction and `R < 0.50` | 0.50× |
| Opposite directions | 0.25× |
| Missing model/threshold history or ineligible asset | 0.00× |

The combined score is `C = S × multiplier`. That score enters the existing portfolio sizing engine; **1.00× means full structural size, not 100% of account capital**. The portfolio is not renormalized to full investment after a score is reduced.

Example: `S = +1`, a positive ML forecast and `R = 0.75` permit full structural long sizing. The same trend with a negative forecast receives quarter sizing. Sigma chooses direction; ML cannot open an opposing standalone position.

Scores update every six hours at 00/06/12/18 UTC, with structural direction changes and missing-data reductions processed hourly. Material weight bands suppress small adjustments. Exit and sizing decisions follow structural direction, volatility and portfolio risk; the draft's unsupported confidence percentages and ATR bracket rules are not represented as implemented behavior.

**Why a soft combination?** The strict “both scores must exceed a threshold” alternatives are implemented and retained, but repeated exits and re-entries often consumed their edge in fees. Entry-only confirmation was also tested. The soft 72h variant won the declared validation ranking; it was not chosen for its later 2026 return.

## Selection discipline and what was actually improved

The original plan recorded **18 hybrid candidates**: three forecast horizons with strict thresholds, a strong-gate filter, an opposing-signal veto and soft sizing. Six entry-only confirmation variants were added after observing turnover losses in 2025 validation, before the new 2026 performance evaluation. This was an adaptive extension, making **24 hybrid candidates**, not 24 independently preregistered tests.

Selection required positive return in **both independently reset 2025 half-years**, at normal and doubled costs, with at least 5% average gross exposure. Candidates were ranked by their worst daily Sharpe across those four cases minus twice their largest drawdown. **Five candidates passed.** The selected parameters and source hashes were frozen before evaluating 2026 strategy performance.

The selected hybrid's worst half-year/cost case returned **+1.46%**. Its full-year validation drawdown improved from **6.88% to 3.98%**, with lower return and lower exposure. In 2026 it returned less than Sigma alone, paid more fees and had slightly greater drawdown. That result argues for keeping the simpler core as the current primary candidate while treating the ML overlay as research.

Audit files: [experiment plan and amendment](research/hybrid/EXPERIMENT_PLAN.md), [all validation candidates](research/hybrid/validation_all_candidates.csv), [frozen selection](research/hybrid/selection_frozen.json), [final comparisons](research/hybrid/final_evaluation.csv) and [training metadata](research/hybrid/models/h72_metadata.json). Initial 18-candidate results remain alongside the expanded study.

## Cost robustness and tradability

All historical orders execute at the **next candle open after the signal candle completes**, with adverse slippage and half a Roostoo price tick. Quantities round down to the configured exchange steps. Allocation changes normally need $50; risk reductions and final liquidation are accounted for separately. Short exposure reserves 1× collateral, and borrowed proceeds cannot finance purchases. Final exit commissions are charged.

The base case assumes **10 bp taker commission per side** for both long and short orders, plus **1 bp adverse slippage per side** and the tick spread proxy. It does not assume every limit fills, maker rebates, fee-rounding credits, latency arbitrage or one-tick profits. Spot fee rates must be verified for the actual account; old API examples are not a universal fee schedule.

| Frozen hybrid, 2026 case | Net return | Maximum drawdown |
|---|---:|---:|
| Base: 10 bp fee + 1 bp slippage per side | +6.06% | 5.90% |
| 20 bp fee + 2 bp slippage per side | +3.53% | 7.22% |
| 10 bp fee + 5 bp slippage per side | +5.14% | 6.38% |
| A real additional one-hour signal/execution delay | +5.46% | 5.22% |
| 1 bp per 8h short carry proxy | +5.73% | 6.14% |
| Long-only feasibility case | +3.91% | 7.73% |

`one_extra_hour` delays the information used by the trading decision by a real additional hour before execution. The inherited engine's confusingly named `delayed` enum is used only for the separate five-basis-point slippage case. Short carry is a real-venue feasibility proxy, not a claim that Roostoo currently charges funding.

At $100,000 starting capital, the largest simulated order was **0.90%** of its asset's last completed Binance hourly quote volume; the 95th percentile was **0.27%**. None of the 606 fills exceeded 1% of that proxy. This is an encouraging size check, **not proof of executable depth**: Binance USDT volume is not Roostoo USD liquidity. The engine does not reconstruct queue priority or market impact from candles.

### Roostoo implementation mapping

| Requirement | Implementation |
|---|---|
| Completed historical candles | External Binance hourly feed; `/v3/ticker` alone cannot produce the model's full feature history |
| Spot entry / exit | Signed `/v3/place_order`, BUY / SELL |
| 1× collateralized shorts | `/v6/short_open` with collateral; `/v6/short_close` with reduce-only `close_qty` |
| Balances and sizing | `SpotWallet` and legacy `Wallet` handling, live precision and `MiniOrder` metadata |
| Order lifecycle | Write-ahead intent, confirmed status/quantity/fees, pending-order reconciliation, no blind mutation retry |
| Model consistency | The same `HybridPolicy` and feature builder in backtest and execution adapter; model/policy identity persisted |
| Ownership and restart safety | Separate hybrid journal, shared single-instance lock, rejection of unmanaged holdings/orders |

The new adapter defaults to shadow, uses the existing all-taker execution path and creates no synthetic maker-fill credit. This update made **zero signed API mutations**. Earlier API tests in [SIGMA_GATE_RESEARCH.md](SIGMA_GATE_RESEARCH.md) establish endpoint/accounting behavior for that tested account; they do not establish profitability of the new ensemble. New-policy end-to-end mock execution remains to be forward-tested.

## Portfolio risk and operational boundaries

The allocation targets at most **95% gross exposure**, at most **40% each for BTC/ETH** and **30% each for other assets**; short structural weights are halved. A fast 12h volatility estimate can reduce sizing relative to the 72h estimate. The rolling 14-day drawdown governor begins reducing risk at 3% drawdown and reaches quarter risk at 10%.

**Continuous backtest results disable the absolute competition stops.** The existing execution configuration separately has +0.35% daily / +1.10% total profit stops, −0.75% daily / −2.50% total loss or peak-drawdown stops, and a three-loss daily halt. Those limits persist in the journal and would produce a different return path. They are client controls subject to quote gaps and API availability, rather than exact guaranteed exit prices.

The paper adapter defaults to a **$100 aggregate exposure cap** for verification. That cap is not the $100,000 research portfolio. The inherited configuration also contains the original competition dates; inspect the session clock and risk settings before a supervised forward run. Preserve runtime journals and reconcile existing exposure when changing strategies.

## Evidence limits to present alongside the results

- **No reproduced +25.01% / +16.27% claims:** those figures in the supplied draft have no supporting attached datasets or run artifacts. The original script, draft and old repository README are preserved for provenance.
- **Concentrated gains:** 5/10 measured months were profitable. August returned **+10.47%**. Zeroing the three best daily returns changes the 2026 compounded return to **-3.63%**. This is a sensitivity diagnostic, not a separately executable strategy.
- **Wide uncertainty:** a 2,000-repetition, seven-day paired moving-block bootstrap gives a 90% hybrid return interval of **-8.09% to +26.86%**, and an interval of **-1.12 to +0.35** for its daily-Sharpe difference versus Sigma. Both span zero. Fourteen-day blocks tell the same qualitative story; these intervals are not adjusted for model/policy selection.
- **Earlier use of the data:** upstream Sigma and earlier local research used parts of 2025–2026. Additional truly unseen forward results are needed before claiming an independent edge.
- **Venue and execution basis:** hourly Binance USDT candles proxy Roostoo USD trading. Bid/ask history, depth, serial API latency, outages and exact matching are not reconstructed. The extra-hour and cost stresses address only some of these uncertainties.

The five compressed input hashes were verified. Because the August gains are consequential, all five August 2026 official Binance archives were independently downloaded again, checked against their published checksums and matched to the attached prices and volumes. See [source recheck](research/hybrid/source_recheck.json).

## Reproduce and inspect

Python 3.11+; run commands from the extracted `roostoo_sentinel` directory. No keys are required for the historical work.

```bash
python -m pip install -r requirements-hybrid.txt
python -m unittest discover -s tests

# Refit the selected five-seed model, reproduce its predictions and compare NAV.
python research/reproduce_hybrid.py --refit --output research/reproduced_hybrid

# Inspect a historical score snapshot; it never sends an order.
python research/hybrid_score.py

# Rebuild charts, statistics and this README from the retained results.
python research/run_hybrid.py --stage report
python research/write_hybrid_readme.py
```

The reproduction receipt verifies input hashes, purged training, identical predictions and both strategies' 2025/2026 returns. The current suite has **170 passing tests**, including future-data invariance, gaps, prior-only ranks, label timing, confirmation logic, true delay, fees, cash conservation and adapter/policy parity.

For a public-data shadow plan, after inspecting the session configuration:

```bash
python -m sentinel.hybrid_live --mode shadow --once
```

Shadow is planning, not an account return simulator. The adapter also exposes bounded paper-exchange mode with private environment credentials; it was not invoked in this research update. Never put keys in a README, notebook, model file or Git commit.

### Code and review artifacts

| Path | Purpose |
|---|---|
| `sentinel/hybrid.py` | Hourly causal features, purged training, five-seed models, trailing ranks and all combination policies |
| `sentinel/hybrid_live.py` | Same frozen policy connected to the reconciled Roostoo adapter |
| `sentinel/sigma.py` / `sentinel/sigma_backtest.py` | Structural allocation, shared cash/collateral ledger and execution stresses |
| `research/run_hybrid.py` | Staged fitting, validation selection, freezing, later evaluation and stress runs |
| `research/reproduce_hybrid.py` | Independent raw-data/model refit and exact return verification |
| `research/hybrid/models/` | Portable LightGBM text models and training metadata |
| `research/hybrid/presentation_metrics.csv` | Consistent headline metrics and controls |
| `research/hybrid/runs/soft_h72_q50_s33/test_2026/base/` | NAV, every simulated fill, every decision and closed episode P&L |
| `research/hybrid/monthly_returns.csv` / `instrument_attribution.csv` | Temporal and instrument attribution, including losses |
| `research/hybrid/uncertainty.json` | Seven- and fourteen-day block-bootstrap results |
| `research/hybrid/originals/` / `README_LEGACY.md` | Unmodified supplied script/draft and earlier documentation |

Primary references: [Roostoo API](https://github.com/roostoo/Roostoo-API-Documents), [Binance public archive specification](https://github.com/binance/binance-public-data), [LightGBM 4.6 parameters](https://lightgbm.readthedocs.io/en/v4.6.0/Parameters.html), and the attributed [Sigma Gate source](https://github.com/DhmalTPS/Sigma-Gate-Crypto-Trend-Allocation). The pinned Sigma components retain their MIT attribution and license under `sentinel/sigma_origin/`; see [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).
