# Roostoo Sentinel

A selective, cost-aware trading research package and Roostoo execution bot for the October 2026 competition. The Sigma extension reuses credited MIT components from DhmalTPS; see `THIRD_PARTY_LICENSES.md`.

## Sigma Gate update, 10 October 2026

`sigma_config.json` was frozen before the new 2026 test. It trades BTC, ETH, SOL, BNB and XRP using 126/252/540-hour trend gates, a 20% annual volatility target, a fast volatility shock guard and six-hour sizing. Direction changes and risk reductions are processed each hour. Default execution uses taker costs because passive matching remains unverified on this account.

The causal 2026 simulation returned **+9.58% after costs, versus +8.61% for the corrected original gates**, with maximum drawdown **5.71% versus 10.41%**. These continuous comparisons do not enable the absolute competition stops. With those stops, the conservative version exceeded 1% in **8 of 20** independently reset 14-day episodes. It lost **1.14%** in the recent seven-day continuous test. Read [SIGMA_GATE_RESEARCH.md](SIGMA_GATE_RESEARCH.md) before using either configuration. Future or competition profitability is unproven.

```bash
python -m sentinel sigma-run --mode shadow --once
python -m sentinel sigma-backtest --start 2026-01-01T00:00:00Z --end 2026-10-10T00:00:00Z --model all_taker
```

With private test keys set as `ROOSTOO_API_KEY` and `ROOSTOO_SECRET_KEY`, read-only inspection and a bounded test are:

```bash
python -m sentinel sigma-status --remote
python -m sentinel sigma-run --mode paper-exchange --max-minutes 3 --portfolio-cap-usd 100
```

The $100 limit is aggregate portfolio exposure. Cleanup may run beyond three minutes; the corrected adapter checks the entry deadline immediately before sending an opening request. One unresolved order pauses other writes. Account ownership, short collateral, partial fills and actual fees are reconciled before further trading. Existing holdings from another runner require their original journal and cannot be adopted automatically. Do not run the Sigma, tick and hourly bots concurrently.

The package includes all source, 160 regression tests, five compressed hourly datasets, raw new study results and actual API execution evidence. Large legacy minute datasets and raw download archives can be recreated with the included scripts. `sigma_reference_config.json` provides the original gate baseline; `sigma_shorter_research_config.json` is a research alternative. Reports update approximately every minute under `runtime/reports/sigma-paper-exchange/` and remain local. No external messages are sent.

The sections below describe the earlier tick and hourly work. Their results do not measure the Sigma extension.

**Earlier execution and research update:** authenticated test-account checks found the live SpotWallet schema, a pending-quantity echo combined with cent-truncated balances, and TAKER fees on crossing LIMITs. All are handled. The tick configuration uses a quiet-market pullback with thirty minutes of real quote history and a movement gate. See [PAPER_EDGE_RESEARCH.md](PAPER_EDGE_RESEARCH.md) for those order checks and comparisons. Historical simulations do not establish account returns.

**Profit and loss controls:** the tick runner now halts at +1.10% total profit, +0.35% daily profit, −0.75% daily loss, −2.50% total loss or drawdown, and three consecutive losing cycles in a day. It cancels and reconciles an owned order before liquidating remaining inventory. Stops use estimated liquidation costs and persist in the existing ledger. [RISK_ENGINE.md](RISK_ENGINE.md) explains reset behavior, the stronger profitability gate and its limits.

**Status: execution works; profitability is unproven.** The new study added 117 development runs and 81 new-day checks. No candidate survived every fill assumption. A real resting buy remained unfilled despite last-price touches; crossing LIMIT buy/sell orders filled as takers and lost $0.09. Complete matching rules and a profitable passive cycle remain unverified. The original hourly configuration stays separate and did not establish profitability or activity compliance.

Read [STRATEGY_RESEARCH.md](STRATEGY_RESEARCH.md) for the earlier studies, applicable rules and instrument selection. The PDFs in `output/pdf/` also describe those earlier studies; [PAPER_EDGE_RESEARCH.md](PAPER_EDGE_RESEARCH.md) is the current execution and strategy assessment.

## New sequential tick candidate

The user clarified that sequential tick scalping is permitted. `tick_config.json` enables PEPE only and rests a buy no higher than the bid, one tick below ask, and one tick below last. It requires a quiet thirty-minute history and actual executable quote movement, then submits a fee-aware sell above the confirmed fill. It owns one position and one order, starts at $100, and scales only after at least twenty current-policy completed cycles pass conservative net-return and loss-aware win-rate checks. Closed paper cycles include a four-cent uncertainty allowance for validation. Negative expectancy or failure to verify after sixty cycles halts the candidate. Risk latches survive configuration changes and restarts. The optional `tick_config_watchlist.json` adds SHIB and WLFI as an unvalidated extension; each pair needs its own fill evidence. Shadow observations never qualify a paper-exchange account.

```bash
python -m sentinel tick-run --mode shadow --once
python -m sentinel tick-run --mode shadow
```

With private competition keys configured in the EC2 environment, the continuous mock-exchange command is:

```bash
python -m sentinel tick-run --mode paper-exchange
```

Use `deploy/roostoo-tick.service` for this alternative. Do not run both services. Preserve `runtime/tick_paper_exchange.sqlite3` and examine actual roles, fees and net cycles. The three bounded test orders above were placed while preparing this update; no competition deployment occurred. The full-size historical simulations omit the initial small-order stage. For bounded verification, use `python -m sentinel tick-run --mode paper-exchange --max-minutes 120 --max-entries 3`; it completes owned cleanup before stopping, subject to API availability. A later explicit run can resume a cleaned-up test session without erasing any risk halt.

Read-only diagnosis with the same private environment variables:

```bash
python -m sentinel tick-status --mode paper-exchange --remote --output runtime/diagnostics
```

The running tick bot updates `runtime/reports/paper-exchange/summary.txt`, `trade_logs.csv`, and `status.json` approximately every minute. Shadow reports go to `runtime/reports/shadow/`. Confirmed order records and closed cycles are counted separately from attempted entries. `tick_order_rejected` includes the redacted exchange error; `tick_order_status` shows pending prices and quote distances; `tick_decision` explains every idle gate. Export files stay on the machine; no external messaging is configured.

Reports also show risk thresholds, baseline and daily returns, persistent halt reasons, and the current policy's profitability evidence. `python research/validate_risk_policy.py` reproduces a conditional risk-overlay diagnostic on the earlier simulated trades; it is not an execution backtest of the corrected policy.

Reproduce the new analysis with `python research/download_limit_data.py`, `python research/evaluate_tick_cycles.py`, and `python research/validate_tick_candidate.py`. These use the included compressed minute candles and archive checksums. The download dates are explicit research endpoints, not rolling current-date defaults.

## Package contents

| Component | Purpose |
|---|---|
| `sentinel/data.py` | Checksummed Binance archives, hourly public data and strict candle validation |
| `sentinel/signals.py` | BTC-regime-conditioned pullback, breakout and range-reversion hypotheses; optional slower swing trend |
| `sentinel/research.py` | Completed trade labels, fee-aware historical outcomes and block-bootstrap admission gate |
| `sentinel/backtest.py` | Portfolio sizing and independent 14-day retrospective competition episodes |
| `sentinel/api.py` | Official Roostoo signing, price/amount precision and v6 short endpoint support |
| `sentinel/account.py` | Verified account binding so test and competition ledgers remain separate |
| `sentinel/execution.py` | Write-ahead intents, fill reconciliation and account ownership checks |
| `sentinel/state.py` | Durable SQLite state, fill journal, configuration/commit audit events |
| `sentinel/risk.py` | Risk sizing, correlated exposure caps, stop/target/time exits and portfolio loss limits |
| `sentinel/runner.py` | Continuous monitoring, background data refresh, single-instance lock, activity reports |
| `sentinel/tick.py` | Quiet pullback limits, quote-quality gates, fee reconciliation and explicit decision diagnostics |
| `research/probe_marketable_limits.py` | Bounded, cost-incurring test-account LIMIT role classification; not the strategy |
| `sentinel/tick_risk.py` | Persistent profit/loss stops, complete-cycle evidence and conservative scaling checks |
| `sentinel/tick_report.py` | Read-only account diagnosis and periodic summary/trade exports |
| `deploy/roostoo-sentinel.service` | EC2 systemd example, initially in paper mode |
| `tests/` | Critical behavior tests, including timeout/restart and label leakage cases |
| `research/` | Actual development failures, retrospective results, benchmarks and test output |
| `data/` | Historical candles used in the research, plus their archive provenance manifest |

## Local installation and verification

Python 3.11 or later is required. Commands below are run from this directory.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m sentinel preflight
python -m sentinel collect
python -m sentinel run --mode paper --once
```

The collector and public preflight make no trading requests. Paper mode requires no trading credentials and uses a separate simulated ledger. `--once` is prohibited in live mode. A paper iteration staying in cash is an expected outcome when no signal passes, not a sign that the process is broken.

The archive contains research candles. To reproduce from the original published files:

```bash
python -m sentinel download --start 2025-01-01 --end 2026-09-30
python -m sentinel backtest --start 2026-01-01 --end 2026-10-01 --output research/reproduced_default
python -m sentinel backtest --start 2026-01-01 --end 2026-10-01 --output research/reproduced_cost_150 --cost-multiplier 1.5
python -m sentinel evidence --as-of 2026-10-01T00:00:00Z
```

`--end` is exclusive for backtests and inclusive for archive downloads. The monthly downloader accepts completed months. The backtester uses whole, independent 14-day windows and omits a final partial window. Research outputs name the evaluated start and end of each window. Never call a zero-trade period a high win rate or assign it an infinite Sharpe/Sortino/Calmar ratio.

## Decision logic

1. Collect completed hourly candles only; reject gaps, stale data and malformed OHLC.
2. Generate a proposed directional setup on a whitelisted instrument.
3. Check the trailing historical outcomes for that pair, setup and direction. Admit only if all sample-size, win-rate, net-mean and bootstrap requirements pass.
4. Check executable spread, liquidity, concurrent Binance/Roostoo basis, cooldown, portfolio limits and the configured competition clock.
5. Size from the loss to the stop plus round-trip costs; submit at most one directional market entry.
6. Monitor existing positions independently of the background data worker. Exit at the observed stop/target, time limit, exposure breach, portfolio loss breach or approaching cutoff.

The rank score is a heuristic priority score. It is **not** an estimated probability of profit. Historical win rates and confidence bounds are descriptive admission checks, not guarantees. A cost-aware target distance is not itself a forecast of expected return.

The initial whitelist is BTC, ETH, BNB, SOL, XRP, LINK, AVAX, AAVE, SUI and LTC versus USD. Only cells with passing evidence are eligible to trade. Newly added tokens, tokenized equity symbols and scaled meme-token mappings are not inferred automatically. PAXG would require a separate commodity regime model and its own validation.

## Risk and execution defaults

| Setting | Default |
|---|---:|
| Planned risk per entry, including modeled costs | 0.15% of equity |
| Aggregate planned stop risk | 0.45% |
| Maximum gross allocation | 50% |
| Maximum single-asset allocation | 20% |
| Same-direction correlated group cap | 30% |
| Maximum concurrent positions | 3 |
| Maximum new entries per HKT day | 4 |
| Daily equity loss halt | 0.75% |
| Peak-to-current equity drawdown halt | 2.5% |
| Stop / target | 1.6 / 2.4 entry-time hourly ATR |
| Maximum holding time | 24 hours |
| Symbol cooldown after closing | 4 hours |
| Risk/quote monitoring cadence | approximately 60 seconds |

The slower swing strategy uses 2.5 times hourly ATR as its volatility unit and evaluates entry opportunities every six hours. It is implemented but not enabled in `config.json`; its tests did not support recommending it. Optional short support is also disabled by default. Enabling it requires both competition permission and verified collateral accounting. A spot SELL never opens a short.

The original hourly runner models market fees with an additional five basis points of friction per leg and uses market orders as its execution benchmark. The separate tick runner rests sequential limits and verifies the actual role and commission before allowing more entries. Neither runner assumes a profitable or guaranteed fill. No unrestricted two-sided quoting, latency arbitrage or high-frequency module is included.

Planned loss limits are not hard guarantees. Client-managed stops can miss a price during an outage or a gap. A network timeout leaves the exchange write status unknown. The bot pauses further writes, persists the intent and reconciles account state/order history instead of blindly retrying. An unresolved write can also delay exits. An ambiguous result that cannot be matched uniquely requires investigation of read-only history and the journal; restarting or deleting the database does not resolve it.

## EC2 deployment example

Use the organizer's supplied account and current account-specific guide. The linked Notion/Pitch pages could not be retrieved during this research; this is a generic EC2/systemd recipe, not a verified substitute for those account instructions.

1. Place this directory at `/home/ubuntu/roostoo_sentinel` on an Ubuntu EC2 instance with a supported Python version and sufficient outbound access to the public data and Roostoo endpoints.
2. Create the virtual environment and install dependencies as above.
3. Copy `.env.example` to `.env`, enter the two keys privately on EC2, and run `chmod 600 .env`. No real credential is included in this package. Do not add `.env` to Git.
4. The service reads `.env` through `EnvironmentFile`. For direct CLI authenticated checks, export the variables privately in the shell; the CLI does not automatically parse `.env`.
5. Run the authenticated read-only preflight and remote activity audit. Inspect balances, existing holdings, pending orders, current fee records and short access. Do not run a new live instance over the old bot or discard its position state.
6. Start the service in paper mode and inspect the journal. Historical tests here do not justify live activation; establish a strategy that passes your validation and confirm the open activity/cutoff questions first.

```bash
python -m sentinel preflight --authenticated
python -m sentinel audit --remote --output runtime/remote_activity.json
sudo cp deploy/roostoo-sentinel.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now roostoo-sentinel
journalctl -u roostoo-sentinel -f
```

When a justified live configuration is ready, change `--mode paper` to `--mode live` in the service and restart it. Preserve `runtime/live.sqlite3` on every redeployment. The sample account user/path must be adjusted for a different AMI username. Keep the competition clock and initial-equity reference accurate for your account. The configured HKT midnight cutoff is provisional; exact scoring cutoff was not published in the retrieved event description.

Stopping the service does not flatten exchange holdings. The continuous runner initiates exits near the configured cutoff, but an outage can prevent them. Monitor service health, reconciliation warnings, exposure and activity.

## Honest activity auditing

```bash
python -m sentinel audit --mode paper
python -m sentinel audit --remote
```

Paper fills are not competition trades. The remote command reads actual signed order history; it never places an order. The local live journal contains this bot's fills only. Earlier trades by a different bot require the remote history to count them. The configured daily minimum of one is a provisional counter, not an organizer-confirmed definition of an active day. The bot produces no quota-only trades. The current conservative configuration is likely unsuitable as the sole bot for satisfying the activity screen.

## Source control and judging

The package includes a genuine local Git history for this implementation. It does not recreate trades or commits before development. Push this original project to your team's repository, follow the stated submission deadline, and commit every later strategy/configuration change with a reason. Secrets, runtime balances and local data caches are excluded by `.gitignore`.

The source archive is a development deliverable, not a published GitHub submission. No authenticated competition order or AWS deployment was performed while producing it. See `research/test_results.txt`, `research/public_preflight.json`, and `research/validation_comparison.json` for the verification actually completed.
