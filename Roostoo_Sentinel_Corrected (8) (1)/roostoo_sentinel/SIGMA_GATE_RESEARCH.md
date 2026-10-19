# Sigma Gate improvement and execution evidence

Research completed 10 October 2026. All returns below are historical simulations unless explicitly labeled authenticated execution. The supplied test credentials were used for current Roostoo account, fee and order checks; they were not used to fabricate historical exchange fills.

**Finding:** the frozen conservative variant improved the tested return-to-risk profile. From 1 January through 9 October 2026 it returned **+9.58% after costs**, with **5.71% maximum drawdown**, compared with **+8.61% and 10.41%** for the corrected original trend model. Double-cost stress remained positive. This does not establish a reliable competition strategy: the recent seven-day test lost **1.14%**, and only **8 of 20** separate 14-day simulations exceeded 1% with the profit/loss stops enabled. The uncertainty intervals include zero.

## What was reused and changed

The user-supplied [Sigma Gate repository](https://github.com/DhmalTPS/Sigma-Gate-Crypto-Trend-Allocation) was pinned at `64e0856d99791aab00c175687d1b354ae1490c8a`. Its published v1.1 trend model and its current v1.5 configuration are different strategies. v1.1 uses 168/336/720-hour trend gates, a 0.5-sigma deadband, inverse volatility allocation, half-sized shorts, volatility scaling, no-trade bands and a drawdown governor. The current configuration instead uses a fixed 90% long basket with a small profit lock. Its recovery objective starts from the upstream team's own contest drawdown and cannot be copied as a performance guarantee for our account.

The original MIT strategy, portfolio, backtest and metric modules are preserved under `sentinel/sigma_origin/`, with the original license and attribution. The new shared policy is `sentinel/sigma.py`, the causal research engine is `sentinel/sigma_backtest.py`, and the Roostoo adapter is `sentinel/sigma_live.py`. No upstream credentials, account state or live logs are imported. Earlier documents saying the bot contains no copied third-party code describe the earlier implementation; the Sigma extension supersedes that claim.

Reproducing the unmodified v1.1 components on independently downloaded data returned **+21.13%, Sharpe 0.946 and maximum drawdown 10.35%** over 25 May 2025 10:00 UTC to 2 October 2026 09:00 UTC, exclusive. This is close to the upstream published +20.87%, rather than a claim of an improvement. That reference engine allows same-close market fills and omits terminal liquidation fees. The new engine corrects those assumptions, so its results must be compared with its own corrected baseline.

The frozen conservative configuration uses:

| Setting | Behavior and purpose |
|---|---|
| Universe | BTC, ETH, SOL, BNB and XRP only; no search across winning coins |
| Trend ensemble | 126, 252 and 540 completed hourly bars; direction changes only beyond a 0.5-sigma deadband |
| Weak gates | Exposure follows the ensemble's absolute conviction; weak agreement leaves cash rather than renormalizing the remaining assets to full investment |
| Volatility | 20% annual target; risk sizing uses the larger of 12-hour and 72-hour EWMA volatility |
| Sizing schedule | Every six hours; direction changes and necessary risk reductions are processed hourly |
| Exposure | Gross at most 95% of NAV, BTC/ETH at most 40% each, other coins at most 30%; shorts receive half-size targets |
| Trading bands | Avoid small weight adjustments; apply exposure ceilings again after the bands |
| Drawdown governor | Reduce exposure as the rolling 14-day drawdown moves from 3% to 10%; scaling never adds leverage |
| Execution | Default taker execution; optional passive orders with reconciliation and a one-hour timeout |

The intended economic source of profit is persistence in multi-day trends. The deadband and sizing schedule aim to avoid paying fees for small reversals; volatility sizing reduces the position when the market becomes more dangerous. Trending markets can produce positive net returns. Choppy reversals produce losses, as the recent test demonstrates. This is an allocation strategy, not a promise that individual trades will mostly win.

## Data, selection and fill assumptions

The new study uses **330 official Binance spot hourly archives**, each verified against its published SHA-256 checksum: 285 monthly archives from January 2022 through September 2026, plus 45 daily archives for 1–9 October 2026. There are **41,831 bars per coin**, 209,155 observations in total. `data/sigma/processed_manifest.json` records the compressed and uncompressed hashes; the original archive manifests record source URLs and checksums.

All five histories have a real missing hour at **24 March 2023 13:00 UTC**. It is not forward-filled. Trend and volatility histories restart on the next contiguous segment and must complete their warmup again. Existing simulated exposure is reduced at the first observed opening price; this cannot reconstruct trading during the missing hour.

`research/sigma_gate/EXPERIMENT_PLAN.md` was committed before evaluating the new candidates. Nine named candidates were compared, including the current fixed-long baseline. Development used 1 March 2022–31 December 2024, and validation used calendar 2025. Selection required positive development and validation results under both passive and all-taker models, then ranked the lower Sharpe across those cases minus twice validation drawdown. The conservative selection and full parameters were committed before the new 2026 tests. It was not selected for the highest later raw return.

The upstream authors already researched parts of 2025–2026. These chronological tests therefore are **not globally untouched out-of-sample evidence**. Results, including losing candidates and recent periods, are retained instead of choosing a new winner after examining the 2026 test.

The corrected engine decides at a completed candle's close, then executes no earlier than the next candle's open. It includes amount/price precision, cash limits, one-times short collateral, trading fees, a half-tick spread proxy, slippage, estimated liquidation costs and final exit fees. Borrowed short proceeds cannot fund additional purchases. Passive fills require a two-basis-point trade-through proxy and may fall back to taker execution. Historical candles cannot establish Roostoo queue priority or actual maker fills.

Binance trades USDT pairs; the competition trades Roostoo USD pairs. Historical venue basis, actual bid/ask spreads, matching behavior, API delays and serial order submission remain execution uncertainties. The live adapter allows only one unresolved write, whereas the hourly simulation can execute several legs during one bar. The simulation does not add paper-only rounding credits or rebates and does not credit capped short losses; it may therefore overstate losses during extreme short squeezes. These assumptions do not establish an executable arbitrage.

## Results after costs

The following comparison starts each model with $100,000 on 1 January 2026 and ends before 10 October 2026 00:00 UTC. **Absolute daily/total profit and loss stops are disabled for these continuous strategy comparisons.** The allocation drawdown governor remains active. Fees and terminal liquidation costs are included. The live $100 test cap is not applied to these full-portfolio simulations.

| Variant, all-taker model | Net return | Sharpe | Maximum drawdown | Fees |
|---|---:|---:|---:|---:|
| Corrected original gates | +8.61% | 0.72 | 10.41% | $2,438.51 |
| Shorter gates only | +17.40% | 1.32 | 10.08% | $2,750.00 |
| **Frozen conservative variant** | **+9.58%** | **1.13** | **5.71%** | **$1,848.77** |
| Current upstream fixed-long/profit-lock model | +0.25% | 0.44 | 0.53% | $180.45 |

The shorter-gate-only model earned more in this later test, with substantially more drawdown. Its test result was not used to revise the frozen selection. `sigma_shorter_research_config.json` preserves it as a research alternative. `sigma_reference_config.json` preserves the original gate baseline.

| Frozen variant robustness case | Net return | Maximum drawdown |
|---|---:|---:|
| All-taker fees and one-basis-point slippage | +9.58% | 5.71% |
| Passive trade-through proxy | +9.98% | 5.67% |
| Double fees and double base slippage | +7.42% | 6.25% |
| Five-basis-point execution slippage | +8.79% | 5.91% |

The CLI model named `delayed` means the five-basis-point next-open execution stress above; it does **not** impose an additional full-hour delay. Testing all six UTC sizing phases produced +9.58% to +11.29%, with drawdown from 5.54% to 5.99%. This reduces dependence on a favorable clock phase.

The freshly reset **3–9 October** continuous test lost **1.14%** for the frozen variant, compared with **1.83%** for the original gates. Double-cost stress lost 1.25%. The short sample's extreme annualized Sharpe should not be read as a precise forecast.

Twenty nonoverlapping 14-day episodes from 2026 were each reset to new capital and state, with the complete daily and total profit/loss stops and liquidation fees enabled:

| Variant | Profitable episodes | Episodes exceeding 1% | Median return |
|---|---:|---:|---:|
| Original gates | 11/20 | 11/20 | +1.12% |
| Shorter gates only | 10/20 | 8/20 | −0.02% |
| Frozen conservative variant | **9/20** | **8/20** | **−0.43%** |
| Current upstream fixed-long model | 10/20 | 0/20 | −1.14% |

The conservative variant's worst episode lost 2.51%, illustrating that a client stop is not an exact loss guarantee. It did **not** improve the observed competition win frequency. A working activity proxy of at least two simulated fills on eight days was met in 13/20 conservative episodes, but only **2/20** both met that proxy and exceeded 1%. The organizer's definition of sufficient strategy trades is not numerically verified, so this is not a qualification count. Early profit locks can conflict with the eight-active-day requirement; the bot does not manufacture activity trades.

A 1,000-resample paired moving-block bootstrap gives a 90% Sharpe interval of **−1.26 to +3.28** for the conservative variant with 168-hour blocks. The interval for its Sharpe improvement over the baseline is **−0.38 to +1.11**. Both include zero. Those resampling statistics are not a probability of future profits, and the nine selection trials and prior upstream research further limit inference.

## What the API key actually verified

The documented Roostoo endpoints provide present quotes and account/order history, not a documented historical candle/matching replay service. Historical price backtests therefore use the checksummed public data. The supplied credentials authenticated the near-$50,000 **test account**, with real signed mock-exchange requests. No competition deployment occurred.

| Test | Actual observed result |
|---|---|
| Passive BTC buy, order 3451826 | Accepted as MAKER/PENDING, remained unfilled, then cancellation verified; no balance change |
| Short permission/accounting, position 6205 | $20 requested collateral became $19.92 after quantity/cents rounding; opened and fully closed; cash changed by −$0.05 |
| Sigma BTC short, position 6207 | 0.00032 BTC opened at $83,064.01; closed at $83,050.01; both opening and closing fees charged |
| Sigma BNB short, position 6208 | 0.029 BNB opened at $749.88; reduce-only cleanup closed at $750.03; both fees charged |
| Sigma integration account change | $49,999.82 to $49,999.72: **−$0.10**, not a profitable strategy demonstration |
| Final cleanup | **Zero open short positions, zero pending orders, zero non-USD holdings and zero locked USD** |

The first short probe initially rejected its own collateral check because it assumed the requested $20 would remain exactly $20. It was resumed from the journal and cleaned up without another opening request. The adapter now uses the acknowledged position and actual collateral.

The bounded integration test also exposed a deadline bug: reads and reconciliation were slow enough that the older running process opened the BNB position after its three-minute entry deadline. Automatic approval review blocked continuation of that run because it could add further exposure. Read-only inspection established ownership, and a separate **reduce-only** cleanup closed the remaining BNB position. The final adapter rechecks the deadline after reconciliation and immediately before any opening request. A regression test verifies an expired session cannot journal or send an entry. The fixed version was regression-tested; it was not rerun for another paid strategy round trip after cleanup.

The authoritative execution evidence is `research/sigma_gate/bounded_session_execution.json`, `actual_trade_logs.csv`, the original event logs, and `bounded_session_cleanup.json`. Historical simulated trade logs live separately under `comparisons/`; their rows explicitly identify `historical_simulation`.

## Paper exchange features and compatibility

The official [Roostoo API documentation](https://github.com/roostoo/Roostoo-API-Documents) supports signed spot orders, querying and canceling owned orders, and one-times collateralized `/v6/short_open` and `/v6/short_close`, including `close_qty` for partial reductions. The implementation supports both documented `Wallet` and observed `SpotWallet`, omits rejected `offset=0`, checks precision and minimum order values, and retains ambiguous writes for reconciliation rather than blindly resending them.

The maker discount is a legitimate cost advantage **only when an order actually fills as maker**. Crossing LIMIT orders can pay taker fees. The new account's resting BTC order did not fill, so the passive simulation is a hypothesis; default execution matches the all-taker test. Shorts pay 0.10% on each leg and are funded from available cash rather than spendable borrowed proceeds. Short losses are capped by collateral in the documented paper accounting, but that does not make an arbitrary short profitable.

One-tick scalping is especially weak on the five chosen coins. With two 0.05% maker fees, a round trip needs approximately **0.10005%** gross price improvement merely to cover fees, before other friction. BTC's $0.01 tick is far smaller. Some low-priced tokens have larger percentage ticks, but their queue, fill sequence and adverse selection still need verified evidence. The earlier permitted sequential tick candidate remains available separately; combining it with Sigma is not supported by the new backtest. No free-fill, fee-rounding, stale-quote or duplicate-order profit was observed or credited.

The [competition event page](https://luma.com/coghwiyt) and the team's clarified permission for sequential tick scalping remain relevant. Technical API compatibility is established for the tested account. Official acceptance of attributed strategy reuse, precise activity thresholds and the scoring cutoff are separate questions. The configured end is provisionally 18 October 00:00 Hong Kong time; confirm it against the organizers' final instructions. The protected 95% gross ceiling keeps the allocation unlevered.

## Risk controls and operation

The Sigma adapter has persistent **+0.35% daily profit, +1.10% total profit, −0.75% daily loss, −2.50% total loss or peak drawdown**, and **three consecutive losing completed positions** controls. Daily controls reset on the next Hong Kong day; total stops persist across restarts and configuration changes. Estimated exit costs enter the account NAV. A hit pauses entries and reconciles/cancels owned orders before flattening owned exposure. API outages, gaps and slippage can cause overshoot. There are no exchange-native bracket guarantees.

It binds the journal to the credential fingerprint, refuses foreign holdings/orders or funded unverified margin-wallet accounting, checks short collateral without double-counting it, and preserves actual fees and realized short PNL. Pending orders that echo their full requested quantity with zero execution fields are not treated as fills. Partial quantities are tracked; canceled unfilled orders do not reset loss counters. Small unsellable spot dust is reported rather than hidden. The same instance lock prevents the older tick/hourly runner using the same runtime directory from running concurrently.

`--portfolio-cap-usd` is an aggregate exposure budget, default **$100**. Historical full-portfolio returns do not apply to this small verification budget. Increasing it is an explicit sizing decision, not an automatic response to a favorable backtest. The $100 test cannot reasonably target a 1% gain on a $100,000 account. Do not erase the existing runtime journal to bypass a stop or ownership check.

Run from the extracted `roostoo_sentinel` directory, using Python 3.11 or later:

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m sentinel sigma-run --mode shadow --once
python -m sentinel sigma-backtest --start 2026-01-01T00:00:00Z --end 2026-10-10T00:00:00Z --model all_taker --output research/reproduced_sigma
python -m sentinel sigma-backtest --start 2026-01-01T00:00:00Z --end 2026-01-15T00:00:00Z --model all_taker --competition-stops --output research/reproduced_sigma_episode
```

Set private `ROOSTOO_API_KEY` and `ROOSTOO_SECRET_KEY` environment variables outside source files. `ROOSTOO_API_SECRET` is also accepted. Then inspect and, if desired, run the bounded test:

```bash
python -m sentinel sigma-status --remote
python -m sentinel sigma-run --mode paper-exchange --max-minutes 3 --portfolio-cap-usd 100
```

`shadow` makes no account writes and reports target plans; it is not a profit simulator. `paper-exchange` places signed test-exchange orders. Optional `--passive` enables experimental maker-first spot execution; shorts and risk exits remain market orders. Reports update approximately every minute under `runtime/reports/sigma-paper-exchange/`: `summary.txt`, `trade_logs.csv` and `status.json`. They are local exports; no email, Slack or other transmission is configured. Cleanup can run past the entry deadline while it verifies a clear account.

To reproduce the full research, run `research/download_sigma_data.py --help`, then `research/compare_sigma.py --stage develop`, `--stage test`, `--stage stress`, and `--stage episodes` in that order. The included frozen selection and the commit history document the original order of work. `research/reproduce_sigma_origin.py` uses fixed reference bounds, and `research/sigma_confidence.py` reproduces the uncertainty calculation. All **160 regression tests passed**, including candle-prefix causality, missing-hour handling, collateral/cash constraints, complete exit costs, account ownership, partial fills, durable stops and the entry deadline.

The delivery contains source, licenses, compressed processed hourly data and new research evidence. Every candidate's generated NAV, simulated trades and summary are included. Hourly debug diagnostics are bundled for the conservative variant and original baseline; the other candidates' diagnostics can be regenerated with the comparison script. Raw archive caches and large older minute datasets are reproducible downloads. Runtime databases, credentials and Git internals are excluded. `GIT_HISTORY.txt`, the research plan, frozen selection and `SIGMA_CHANGES.patch` preserve traceability without adding a nested Git repository.

The practical conclusion is that Sigma Gate is a better-supported allocation candidate than assuming every permitted paper tick will fill profitably. The conservative version reduced historical risk, but the short-horizon, activity and statistical evidence does not justify calling it a proven competition winner.
