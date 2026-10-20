import os
import numpy as np
import pandas as pd
import lightgbm as lgb
from scipy.stats import spearmanr

HORIZON = 240      # hold length in 1-min bars
PURGE   = 241      # lookahead of the target (t+1 -> t+241)

# ------------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------------
FEATURE_SET   = os.environ.get("FEATURE_SET", "relative")   # "relative" or "full"
SEEDS         = [42, 7, 123, 2024, 99]                       # ensemble to remove seed luck
QUANTILE      = 0.95                                         # trade the top 5% |signal| ...
LOOKBACK_DAYS = 30                                           # ... measured over the trailing 30 days
TAKER_FEE     = 0.0005                                       # 5 bps/side (standard USDT-M taker; check your tier)
HALF_SPREAD   = 1.0e-4                                       # 1 bp/side
FUNDING_8H    = 1.0e-4                                       # charged on both sides = conservative for shorts

DATA_DIRS = ['/kaggle/input/datasets/ayushyadav1012/pepe-dataset', '.', '/mnt/user-data/uploads']
FILES = ['train_pepe.csv', 'pepe_2025_val.csv', 'pepe_2026_test.csv']
USECOLS = ['open_time', 'high', 'low', 'close', 'volume', 'quote_volume',
           'trade_count', 'taker_buy_base_vol']


def find_data_dir():
    for d in DATA_DIRS:
        if all(os.path.exists(os.path.join(d, f)) for f in FILES):
            return d
    raise FileNotFoundError(f"Could not find {FILES} in any of {DATA_DIRS}")


# ------------------------------------------------------------------
# 1. FEATURES
# ------------------------------------------------------------------
def engineer_features(df, feature_set=FEATURE_SET):
    ts = np.where(df['open_time'] > 10**14, df['open_time'] // 1000, df['open_time'])
    df = df.assign(datetime=pd.to_datetime(ts, unit='ms'))
    df = df.sort_values('datetime').drop_duplicates('datetime').reset_index(drop=True)
    c = df['close']

    df['target_4h'] = np.log(c.shift(-PURGE) / c.shift(-1))

    vs = df['volume'].replace(0, np.nan)
    df['taker_buy_ratio']     = (df['taker_buy_base_vol'] / vs).clip(0, 1).fillna(0.5)
    df['taker_buy_ratio_15m'] = df['taker_buy_ratio'].rolling(15, min_periods=1).mean()
    df['taker_buy_ratio_1h']  = df['taker_buy_ratio'].rolling(60, min_periods=1).mean()
    upt = (df['quote_volume'] / df['trade_count'].replace(0, np.nan)).fillna(0.0)
    df['avg_trade_size']      = (upt / upt.rolling(1440, min_periods=60).mean().replace(0, np.nan)).fillna(1.0)
    tc = df['trade_count'].rolling(60, min_periods=1).mean().replace(0, np.nan)
    df['trade_intensity_1h']  = (df['trade_count'] / tc).fillna(1.0)

    df['ret_1m'] = np.log(c / c.shift(1)).fillna(0.0)
    for name, w in [('15m', 15), ('1h', 60), ('4h', 240), ('12h', 720), ('24h', 1440)]:
        df['ret_' + name] = np.log(c / c.shift(w))

    e15, e60, e240 = (c.ewm(span=s, adjust=False).mean() for s in (15, 60, 240))
    df['ema_15_60']    = e15 / e60 - 1
    df['ema_60_240']   = e60 / e240 - 1
    df['price_ema_60'] = c / e60 - 1

    df['vol_1h']  = df['ret_1m'].rolling(60).std()
    df['vol_12h'] = df['ret_1m'].rolling(720).std()
    df['vol_24h'] = df['ret_1m'].rolling(1440).std()
    df['dist_high_24h'] = (df['high'].rolling(1440).max() - c) / c
    df['dist_low_24h']  = (c - df['low'].rolling(1440).min()) / c

    d = c.diff()
    g = d.where(d > 0, 0.0).ewm(alpha=1/60, adjust=False).mean()
    l = (-d.where(d < 0, 0.0)).ewm(alpha=1/60, adjust=False).mean()
    df['RSI_60'] = (100 - 100 / (1 + g / l.replace(0, np.nan))).fillna(50.0)
    pc = c.shift(1)
    tr = pd.concat([df['high'] - df['low'], (df['high'] - pc).abs(), (df['low'] - pc).abs()], axis=1).max(axis=1)
    df['atr_norm'] = tr.ewm(alpha=1/60, adjust=False).mean() / c
    df['vol_ratio_24h'] = (df['quote_volume'] / df['quote_volume'].rolling(1440, min_periods=60).mean().replace(0, np.nan)).fillna(1.0)

    # Volatility-standardised target
    df['expected_4h_vol'] = (df['vol_24h'] * np.sqrt(240.0)).clip(lower=0.005)
    df['target_4h_norm']  = df['target_4h'] / df['expected_4h_vol']

    base = ['taker_buy_ratio', 'taker_buy_ratio_15m', 'taker_buy_ratio_1h', 'avg_trade_size',
            'trade_intensity_1h', 'ret_15m', 'ret_1h', 'ret_4h', 'ret_12h', 'ret_24h',
            'ema_15_60', 'ema_60_240', 'price_ema_60', 'vol_1h', 'vol_12h', 'vol_24h',
            'dist_high_24h', 'dist_low_24h', 'RSI_60', 'atr_norm', 'vol_ratio_24h']

    if feature_set == "relative":
        # Remove volatility *level* (a regime fingerprint) from the inputs: express everything in
        # units of the trailing 24h volatility so the model can't just memorise "which year is this".
        v = df['vol_24h'].clip(lower=1e-5)
        for name, w in [('15m', 15), ('1h', 60), ('4h', 240), ('12h', 720), ('24h', 1440)]:
            df['ret_' + name] = df['ret_' + name] / (v * np.sqrt(w))
        for col in ['ema_15_60', 'ema_60_240', 'price_ema_60']:
            df[col] = df[col] / (v * np.sqrt(60))
        for col in ['dist_high_24h', 'dist_low_24h']:
            df[col] = df[col] / (v * np.sqrt(1440))
        df['vol_1h']   = df['vol_1h']  / v
        df['vol_12h']  = df['vol_12h'] / v
        df['atr_norm'] = df['atr_norm'] / v
        base.remove('vol_24h')

    keep = ['datetime', 'close', 'target_4h', 'target_4h_norm', 'expected_4h_vol']
    if 'vol_24h' not in base:
        keep.append('vol_24h')
    small = df[base].astype('float32')
    lags = {f'{col}_lag{k}': small[col].shift(k) for col in base for k in (15, 60, 240)}
    out = pd.concat([df[keep].reset_index(drop=True), small, pd.DataFrame(lags, index=small.index)], axis=1)
    feats = base + list(lags)
    out = out.replace([np.inf, -np.inf], np.nan).dropna(subset=feats).reset_index(drop=True)
    return out, feats


# ------------------------------------------------------------------
# 2. MODEL (seed ensemble) & IC
# ------------------------------------------------------------------
def fit_ensemble(X, y, seeds=SEEDS):
    alpha = float(np.percentile(np.abs(y), 90))
    models = []
    for s in seeds:
        m = lgb.LGBMRegressor(objective='huber', alpha=alpha, learning_rate=0.03, n_estimators=120,
                              max_depth=4, num_leaves=15, min_child_samples=250, reg_alpha=1.0,
                              reg_lambda=10.0, subsample=0.7, subsample_freq=1, colsample_bytree=0.6,
                              random_state=s, n_jobs=-1, verbose=-1)
        m.fit(X, y)
        models.append(m)
    return models


def predict(models, X):
    return np.mean([m.predict(X) for m in models], axis=0)


def evaluate_ic(df, name, pred_col='y_hat_norm', target_col='target_4h'):
    v = df.dropna(subset=[pred_col, target_col])
    ics = [spearmanr(v.iloc[o::HORIZON][pred_col], v.iloc[o::HORIZON][target_col])[0] for o in range(0, HORIZON, 8)]
    n = len(v) // HORIZON
    se = 1.06 / np.sqrt(max(n - 3, 1))
    print(f"[{name}] IC {np.mean(ics):+.4f} (SE ~{se:.3f}, t ~{np.mean(ics)/se:+.1f}, n_indep={n})")
    return float(np.mean(ics))


# ------------------------------------------------------------------
# 3. SIGNAL THRESHOLD: trailing quantile (adapts to prediction-scale drift, no lookahead)
# ------------------------------------------------------------------
def add_trailing_threshold(df, q=QUANTILE, lookback_days=LOOKBACK_DAYS, min_days=7):
    dt  = df['datetime'].values
    a   = df['y_hat_norm'].abs().values
    day = df['datetime'].dt.floor('D')
    thr = {}
    for d in day.unique():
        d = pd.Timestamp(d)
        lo = np.searchsorted(dt, (d - pd.Timedelta(days=lookback_days)).to_datetime64())
        hi = np.searchsorted(dt, d.to_datetime64())
        thr[d] = np.quantile(a[lo:hi], q) if (hi - lo) >= min_days * 1440 * 0.5 else np.nan
    df['z_thr'] = day.map(thr).values
    return df


# ------------------------------------------------------------------
# 4. BACKTEST WITH CORRECT PER-TRADE STATISTICS
# ------------------------------------------------------------------
def hold_positions(raw, hold=HORIZON):
    pos = np.zeros(len(raw)); entries = []
    i, n = 0, len(raw)
    while i < n:
        if raw[i] != 0:
            e = min(i + hold, n); pos[i:e] = raw[i]; entries.append(i); i = e
        else:
            i += 1
    return pos, entries


def run_backtest(df, name, fee=TAKER_FEE, spread=HALF_SPREAD, funding=FUNDING_8H, verbose=True):
    df = df.reset_index(drop=True)
    cost = fee + spread
    close = df['close'].values
    y, thr = df['y_hat_norm'].values, df['z_thr'].values
    with np.errstate(invalid='ignore'):
        raw = np.where(y > thr, 1.0, np.where(y < -thr, -1.0, 0.0))
    pos, entries = hold_positions(raw)

    # --- per-trade table (entry Close_{t+1}, exit Close_{t+241}); one row = one round trip ---
    rows = []
    for i in entries:
        if i + 1 >= len(close):
            continue
        j = min(i + PURGE, len(close) - 1)
        gross = raw[i] * (close[j] / close[i + 1] - 1.0)
        net = gross - 2 * cost - funding * (HORIZON / 480.0)
        rows.append((df['datetime'].iloc[i], raw[i], gross, net))
    tr = pd.DataFrame(rows, columns=['datetime', 'side', 'gross', 'net'])

    # --- bar-level equity (position known at t, entered at t+1, earns bars t+2..t+241) ---
    p = pd.Series(pos).shift(2).fillna(0.0).values
    bar = np.r_[0.0, close[1:] / close[:-1] - 1.0]
    turn = np.abs(np.diff(p, prepend=0.0))
    net_bar = p * bar - turn * cost - np.abs(p) * funding / 480.0
    eq = np.cumprod(np.clip(1 + net_bar, 1e-8, None))
    mdd = (eq / np.maximum.accumulate(eq) - 1).min()
    sd = net_bar.std()
    sharpe = net_bar.mean() / sd * np.sqrt(525600) if sd > 0 else 0.0

    n = len(tr)
    if n > 1:
        se = tr['gross'].std() / np.sqrt(n)
        worst3 = tr['gross'].nsmallest(3).sum()
        stats = dict(trades=n, gross_mean=tr['gross'].mean(), gross_se=se, t=tr['gross'].mean() / se,
                     net_mean=tr['net'].mean(), win=(tr['gross'] > 0).mean(),
                     ex_worst3=tr['gross'].sum() - worst3, total=tr['gross'].sum())
    else:
        stats = dict(trades=n)
    if verbose:
        print(f"\n--- {name} ---")
        if n > 1:
            print(f"Trades (round trips) : {n} | longs {(tr.side > 0).sum()} / shorts {(tr.side < 0).sum()} | win-rate {stats['win']:.1%}")
            print(f"Gross / trade        : {stats['gross_mean']*100:+.3f}% +/- {stats['gross_se']*100:.3f}% (t = {stats['t']:+.2f})")
            print(f"Net / trade          : {stats['net_mean']*100:+.3f}%  (round-trip cost incl. funding = {(2*cost + funding*0.5)*100:.3f}%)")
            print(f"Sum gross all / excl. worst 3 trades: {stats['total']*100:+.1f}% / {stats['ex_worst3']*100:+.1f}%")
        print(f"Bar-level net return : {eq[-1]-1:+.2%} | MaxDD {mdd:.1%} | Sharpe {sharpe:.2f} | Buy&Hold {close[-1]/close[0]-1:+.2%}")
    stats.update(net_ret=float(eq[-1] - 1), mdd=float(mdd))
    return tr, stats


# ------------------------------------------------------------------
# 5. PIPELINE
# ------------------------------------------------------------------
def main(feature_set=FEATURE_SET):
    d = find_data_dir()
    raw = pd.concat([pd.read_csv(os.path.join(d, f), usecols=USECOLS) for f in FILES], ignore_index=True)
    full, feats = engineer_features(raw, feature_set)
    del raw
    yr, mo = full['datetime'].dt.year, full['datetime'].dt.month
    cut = lambda x: x.iloc[:-PURGE].dropna(subset=['target_4h_norm']).copy()   # purge before each split

    train = cut(full[yr.isin([2023, 2024])])
    h1    = cut(full[(yr == 2025) & (mo <= 6)])      # prediction history for thresholds only
    h2    = cut(full[(yr == 2025) & (mo > 6)])       # out-of-sample #1
    test  = full[yr == 2026].dropna(subset=['target_4h_norm']).copy()  # out-of-sample #2

    ts = train.iloc[::15]
    print(f"Feature set: {feature_set} | {len(feats)} features | training rows: {len(ts):,} | seeds: {SEEDS}")
    models = fit_ensemble(ts[feats], ts['target_4h_norm'])

    oos = pd.concat([h1, h2, test], ignore_index=True)
    oos['y_hat_norm'] = predict(models, oos[feats])
    oos = add_trailing_threshold(oos)

    for nm, part in (('2025 H1', oos.iloc[:len(h1)]), ('2025 H2', oos.iloc[len(h1):len(h1)+len(h2)]), ('2026', oos.iloc[len(h1)+len(h2):])):
        print(f"{nm}: prediction std {part.y_hat_norm.std():.3f} | median trailing threshold {part.z_thr.median():.3f}")
        evaluate_ic(part, nm)

    seg_h2   = oos.iloc[len(h1):len(h1) + len(h2)]
    seg_test = oos.iloc[len(h1) + len(h2):]
    tr_a, st_a = run_backtest(seg_h2,   "2025 H2 OOS")
    tr_b, st_b = run_backtest(seg_test, "2026 OOS")

    pooled = pd.concat([tr_a, tr_b])
    se = pooled.gross.std() / np.sqrt(len(pooled))
    print(f"\n=== POOLED H2-2025 + 2026: {len(pooled)} trades | gross {pooled.gross.mean()*100:+.3f}% +/- {se*100:.3f}% "
          f"(t = {pooled.gross.mean()/se:+.2f}) | net {pooled.net.mean()*100:+.3f}% ===")

    print("\nCost stress (2x fees & spread):")
    run_backtest(seg_h2,   "2025 H2 OOS (2x cost)", fee=2*TAKER_FEE, spread=2*HALF_SPREAD)
    run_backtest(seg_test, "2026 OOS (2x cost)",    fee=2*TAKER_FEE, spread=2*HALF_SPREAD)
    return oos, models, feats


if __name__ == "__main__":
    main()