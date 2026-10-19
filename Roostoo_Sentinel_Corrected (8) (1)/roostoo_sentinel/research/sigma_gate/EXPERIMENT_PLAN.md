# Plan recorded before new Sigma Gate results

Pinned upstream: `64e0856d99791aab00c175687d1b354ae1490c8a`.
The published v1.1 trend model and current v1.5 fixed-long model are distinct
baselines. Their published returns will not be attributed to a new variant.

Universe remains BTC, ETH, SOL, BNB and XRP. No instrument search. Hourly
closed bars, checksum-verified monthly Binance archives from January 2022
through September 2026, plus completed October days. USD/USDT basis and venue
matching remain limitations. No invented historical Roostoo fills.

Chronological development: 2022-03-01 to 2024-12-31.
Validation: calendar 2025. Freeze a small shortlist using these periods, then
test 2026 separately. The upstream team already studied much of 2025–2026;
the copied hypothesis is not globally untouched out-of-sample. Recent
October results are diagnostics and will not be used to tune parameters.

Candidate changes, decided from mechanics rather than winning backtest rows:

1. Hard gross/per-coin caps after applying no-trade bands; enforce cash,
   collateral, quantity precision and exit costs in the research engine.
2. Rebalance sizing every six hours while processing direction changes and
   urgent risk reduction hourly. Test all six UTC phases, not a lucky hour.
3. Volatility-shock sizing: use the larger of the 12-hour and 72-hour EWMA
   volatility and a covariance exposure ceiling. Avoid instant risk increases
   when stale volatility misses a jump.
4. Direction-aware covariance sizing against the actual signed basket instead
   of a hypothetical fully long basket; never add leverage to exploit netting.
5. Strong-gate admission: hold cash for a split/weak trend ensemble; retain the
   dead-band. This may reduce fee-consuming whipsaws at the cost of missed
   trends.
6. Cost-aware execution: compare passive trade-through fills with all-taker
   costs and delayed next-open fills. Assume 0.05% maker only for simulated
   nonmarketable fills; crossing LIMITs and shorts pay 0.10%.

Use a small named ablation set, not a broad optimisation. Rank development
and validation net performance, drawdown and turnover. Report every candidate
including failures, double-cost stress, phase sensitivity, recent performance
and independently reset 14-day episodes with liquidation charges.

Live test scope: supplied near-$50,000 test wallet only; read-only account and
order inspection plus bounded small execution classification when needed.
No competition deployment, wash-trade activity farming or credits for shadow
profits. Paper matching advantages are hypotheses until observed. Keep test
evidence separate from strategy performance and competition account state.
