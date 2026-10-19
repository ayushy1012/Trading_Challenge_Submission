# Trading_Challenge_Submission
An automated trading bot that combines our main rule-based strategy (**Roostoo Sentinel**)[cite: 1] with a **LightGBM** machine learning model[cite: 2] for the Roostoo Quant Trading Competition.

## 🎯 Strategy Overview

Our system uses two models working together[cite: 3]. **Roostoo Sentinel** tracks live price action, volatility, and order execution to spot immediate trading setups[cite: 1], while **LightGBM** predicts where the price will be in 4 hours[cite: 2]. Instead of relying on just one signal, we only take a full trade when both models strongly agree on the direction[cite: 3].

### Key Principles

- **Two-Model Confirmation**: We filter out bad trades by requiring our main strategy to be >75% confident and our LightGBM model to be >90% confident before entering a full position.
- **Risk-Adjusted Returns**: Designed to keep drawdowns low and protect our Sortino, Sharpe, and Calmar ratios.
- **Long & Short Trading**: Trades both 1x spot long (`/v3/place_order`) and 1x spot short (`/v6/short_open`) positions with no leverage.
- **Lower Fee Execution**: Prioritizes limit orders (0.05% maker fee) over market orders (0.10% taker fee) whenever possible to save on commissions.

---

## 🌐 Trading Pairs

We trade **10 high-volume cryptocurrencies** on Roostoo (excluding stablecoins)[cite: 1, 3]:

- **Major Coins**: `BTC/USD`, `ETH/USD`, `BNB/USD`, `SOL/USD`, `XRP/USD`[cite: 1, 3]
- **Large Altcoins**: `AAVE/USD`, `AVAX/USD`, `LINK/USD`, `LTC/USD`, `SUI/USD`[cite: 1]

We stick to these top coins because they have tight bid-ask spreads, lower slippage, and enough historical price data (`2023–2026`) to properly calibrate our volatility filters and train our LightGBM model[cite: 1, 2, 3].

---

## 🧠 Hybrid Model Architecture

### 1. Main Strategy: Roostoo Sentinel (`roostoo_sentinel`)

**Roostoo Sentinel** is our core rule-based trading engine[cite: 1]. Because the Roostoo API provides real-time ticker snapshots (`/v3/ticker`) rather than standard candlestick charts, Sentinel is built to process live price ticks and manage trades through four connected steps[cite: 1]:

#### Step A: Live Tick & Momentum Analysis (`TICK_STRATEGY`)
- Every cycle, the bot pulls `LastPrice`, `MaxBid`, `MinAsk`, 24-hour `Change`, and `UnitTradeValue` (USD volume) for all 10 coins.
- **Short-Term Momentum**: It compares the latest price against recent rolling price averages to see if a coin is breaking out upward or breaking down.
- **Relative Strength**: It checks how each altcoin is moving compared to `BTC/USD` and `ETH/USD`[cite: 1]. Coins moving strongly in the same direction as Bitcoin get a higher score, while weak laggards are ignored.
- **Spread Check**: It measures the gap between `MaxBid` and `MinAsk`. If the bid-ask spread is too wide, the bot lowers its confidence score so we don't lose money entering a bad price.
- **Output**: Produces a trade direction (`BUY`, `SHORT`, or `HOLD`) and a **Sentinel Confidence Score from 0% to 100%**.

#### Step B: Volatility Filter (`SIGMA_GATE`)
- Before any signal is allowed through, it must pass the **Sigma Gate** (`data/sigma/`)[cite: 1].
- Using historical volatility data for each coin, the bot calculates the current rolling standard deviation ($\sigma$) of returns.
- **Blocking Flat Markets**: If volatility is too low, small price moves won't even cover Roostoo's 0.05%–0.10% trading fees. The Sigma Gate blocks trades during these dead periods.
- **Blocking Extreme Spikes**: If volatility is abnormally high (such as a sudden flash crash or pump-and-dump wick), the gate blocks new entries to avoid buying the top or shorting the bottom.
- Trades are only allowed when volatility sits in a healthy, tradable range.

#### Step C: Limit vs. Market Order Selection (`FILL_CORRECTIONS`)
- On Roostoo, Market orders cost **0.10%** (taker fee), while Limit orders cost **0.05%** (maker fee)—cutting trading costs in half.
- However, a Limit order might not get filled if the price runs away too fast.
- Using historical 1-minute fill data (`data/limit_minutes/`), our **Fill Correction** logic estimates whether a limit order placed at the current `MaxBid` (for buys) or `MinAsk` (for shorts) is likely to fill within the next few minutes[cite: 1]:
  - **Normal Breakout**: Places a `LIMIT` order to pay the cheaper 0.05% fee. If the order stays `PENDING` for too long without filling, the bot cancels it via `/v3/cancel_order`[cite: 1].
  - **Fast Breakout (High Urgency)**: If price momentum is moving too fast for a limit order to catch, the bot switches to a `MARKET` order so we don't miss the move.

#### Step D: Portfolio & Position Guardrails (`RISK_ENGINE`)
- Before sending the order to the exchange, the internal Risk Engine checks our current wallet balances (`/v3/balance`) and open short positions (`/v6/short_positions`)[cite: 1].
- It ensures we have enough free USD, verifies we haven't exceeded our maximum allocation per coin, and rounds the order price and quantity to match Roostoo's exact `PricePrecision`, `AmountPrecision`, and `MiniOrder` rules from `/v3/exchangeInfo`.

---

### 2. Confirmation Model: LightGBM v2 (`lightgbmv2`)

While Roostoo Sentinel is great at catching short-term setups, short-term signals can sometimes get caught in fake breakouts. To fix this, we use a **LightGBM regression model** (`LGBMRegressor`) that predicts the **4-hour forward log return** ($\ln(P_{t+4\text{h}} / P_t)$) to confirm the bigger trend[cite: 2].

#### Features Used:
The model uses standard price and volume indicators calculated from 1-minute data[cite: 2]:
1. **Past Returns**: Log returns over `1m`, `15m`, `1h`, `4h`, `12h`, and `24h` windows[cite: 2].
2. **Moving Averages**: 15, 60, and 240-minute Exponential Moving Averages (`EMA_15`, `EMA_60`, `EMA_240`) and the percentage distance between them[cite: 2].
3. **Volatility & Price Range**: Rolling return standard deviation (`1h`, `12h`, `24h`) and how far the current price is from its 24-hour high and low[cite: 2].
4. **RSI, ATR & Volume**: 60-minute RSI (`RSI_60`), normalized 60-minute ATR (`atr_norm`), and current volume compared to the 24-hour average (`vol_ratio_24h`)[cite: 2].
5. **Lagged Values**: Past values of the indicators above from `15m`, `1h`, and `4h` ago so the model can see how fast indicators are changing[cite: 2].

#### Model Training & Backtest Results:
- **Train / Validation / Test Split**: Trained on `2023–2024` data (`52,512` samples), validated on `2025`, and tested out-of-sample on `2026` data[cite: 2].
- **Overfitting Prevention**: Uses shallow trees (`max_depth=5`, `num_leaves=20`), L2 regularization (`reg_lambda=5.0`), and early stopping[cite: 2].
- **Backtest Performance (including 0.10% trading fees)**:
  - **2025 Validation**: +25.01% return (vs. -5.96% Buy & Hold), **1.66 Sharpe**, **2.41 Sortino**, **3.48 Calmar**[cite: 2].
  - **2026 Out-of-Sample Test**: +16.27% return (vs. -5.23% Buy & Hold), **1.72 Sharpe**, **2.53 Sortino**, **1.49 Calmar**[cite: 2].

---

### 3. Signal Combination Rules

We combine both models using simple confidence rules:

| Roostoo Sentinel Confidence | LightGBM Confidence | Agreement | Action |
| :--- | :--- | :--- | :--- |
| **> 75%** | **> 90%** *(predicted 4h move >= 0.20%)*[cite: 2] | **Both Agree** (Buy or Short) | **Full Position (100% size)**[cite: 3] |
| **> 85%** | **75% – 90%** | **Both Agree** (Buy or Short) | **Half Position (50% size)**[cite: 3] |
| **< 75%** | **< 90%** | **Low Confidence** | **No Trade**[cite: 3] |
| **Any** | **Opposite Direction** | **Disagree** | **No Trade** |

- **Low Trade Frequency**: In testing, LightGBM's >90% confidence threshold ($\ge 0.20\%$ predicted move) triggers on less than **0.65% of minutes**[cite: 2]. Combining it with Sentinel's >75% threshold prevents overtrading, saves on fees, and follows competition anti-HFT rules.

---

## 💰 Position Sizing & Risk Management

### Position Sizing

We manage the **$100,000 portfolio** with strict size limits:
- **Base Trade Size**: 10% to 15% of total portfolio value per trade, scaled by the average confidence of both models[cite: 3].
- **Max Exposure per Coin**: Capped at **20% of the portfolio** for any single trading pair[cite: 3].
- **Short Sizing**: Short trades via `/v6/short_open` use the same USD collateral rules, keeping extra free USD to cover the 0.1% opening fee.

### Risk Management

- **ATR Stop-Loss & Take-Profit**: Stop-loss is set at `Entry Price -/+ (1.5 × ATR)` (capped between 0.5% and 4.5%), and take-profit is set at `Entry Price +/- (2.5 × ATR)` for a ~1.67:1 reward-to-risk ratio[cite: 3].
- **4-Hour Time Limit**: Since LightGBM predicts 4-hour returns, trades are held for up to **4 hours** (`239 minutes`) unless closed earlier by a stop-loss, take-profit, or opposite signal[cite: 2].
- **Drawdown Protection**: If the overall portfolio drops near our maximum drawdown limit, the bot automatically cuts position sizes in half until performance recovers.

---

## 📊 Entry & Exit Logic

### Entries
- **Long Entry (`/v3/place_order`, `BUY`)**: Sentinel is bullish (>75% confidence), Sigma Gate passes[cite: 1], AND LightGBM predicts a 4-hour gain of at least `+0.20%` (>90% confidence)[cite: 2].
- **Short Entry (`/v6/short_open`)**: Sentinel is bearish (>75% confidence), Sigma Gate passes[cite: 1], AND LightGBM predicts a 4-hour drop of at least `-0.20%` (>90% confidence)[cite: 2].

### Exits
- **Stop-Loss or Take-Profit Hit**: Immediately closes the long position (`/v3/place_order`, `SELL`) or short position (`/v6/short_close`)[cite: 3].
- **4-Hour Hold Reached**: Closes the trade after 4 hours if no new signal keeps it open[cite: 2].
- **Signal Reversal**: Closes the open position if both models flip to the opposite direction.
- **Unfilled Limit Orders**: Cancels old pending limit orders via `/v3/cancel_order` to free up locked cash.

---

## 🔄 Bot Execution Loop

Running continuously on our **AWS EC2 instance**, the bot repeats these steps automatically:

1. **Sync Server Time**: Calls `GET /v3/serverTime` and `GET /v3/exchangeInfo` to keep timestamps within the 60-second limit and check order precision rules.
2. **Fetch Prices & Balances**: Calls `GET /v3/ticker` for live market data and `GET /v3/balance` + `GET /v6/short_positions` to check available cash and open trades[cite: 3].
3. **Update Features & Run Models**: Updates the 1-minute price history, calculates technical indicators, and gets predictions from **Roostoo Sentinel**[cite: 1] and **LightGBM** (`lgb_quant_model_1m.pkl`)[cite: 2].
4. **Manage Risk & Place Orders**: Checks open trades for stop-loss/take-profit exits, checks new signals against the **>75% / >90% confidence rules**, sends orders to Roostoo, and logs every trade locally[cite: 3].
5. **Wait**: Sleeps until the next cycle to respect API rate limits[cite: 3].