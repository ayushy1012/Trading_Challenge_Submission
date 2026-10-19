"""Causal Sigma Gate allocation, shared by research and the Roostoo adapter.

The trend-gate function is credited to DhmalTPS (MIT); this adapter adds strict
data validation, named risk/sizing ablations and post-band exposure ceilings.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .data import HOUR, read_bars, validate_frame
from .sigma_origin.strategy.signals import ewm_vol, trend_gate_hysteresis

PAIRS = ('BTC/USD', 'ETH/USD', 'SOL/USD', 'BNB/USD', 'XRP/USD')


@dataclass(frozen=True)
class SigmaConfig:
    name: str = 'sigma-reference-corrected'
    pairs: tuple[str, ...] = PAIRS
    gate_lookbacks: tuple[int, ...] = (168, 336, 720)
    dead_band: float = 0.5
    vol_halflife: float = 72
    fast_vol_halflife: float = 12
    warmup_hours: int = 720
    min_daily_volume: float = 1_000_000
    gate_min: float = 0.30
    short_scale: float = 0.5
    allow_shorts: bool = True
    target_vol: float = 0.30
    gross_cap: float = 0.95
    major_cap: float = 0.40
    other_cap: float = 0.30
    cov_window: int = 336
    cov_shrink: float = 0.30
    shock_guard: bool = False
    signed_covariance: bool = False
    sizing_hours: int = 1
    sizing_phase: int = 0
    dd_soft: float = 0.03
    dd_hard: float = 0.10
    dd_floor: float = 0.25
    band_abs: float = 0.03
    band_rel: float = 0.20
    min_trade_usd: float = 50
    maker_fee: float = 0.0005
    taker_fee: float = 0.001
    maker_patience_seconds: int = 3600
    passive_through_bps: float = 2
    slippage_bps: float = 1
    max_order_fraction: float = 0.25
    probe_total_usd: float = 100
    daily_profit: float = 0.0035
    total_profit: float = 0.011
    daily_loss: float = 0.0075
    total_loss: float = 0.025
    max_drawdown: float = 0.025
    max_consecutive_losses: int = 3
    runtime_dir: str = 'runtime'
    data_dir: str = 'data/sigma'
    base_url: str = 'https://mock-api.roostoo.com'
    data_base_url: str = 'https://data-api.binance.vision'
    spacing: float = 2.2
    poll_seconds: float = 30
    competition_start: str = '2026-10-04T12:00:00Z'
    competition_end: str = '2026-10-18T00:00:00+08:00'
    fixed_long: float = 0
    fixed_floor: float = -0.042
    fixed_goal: float = 0.0025

    def __post_init__(self):
        if tuple(self.pairs) != PAIRS:
            raise ValueError('Sigma universe is fixed; instrument selection is not a tuning parameter')
        if not self.gate_lookbacks or any(type(x) is not int or x < 24 for x in self.gate_lookbacks):
            raise ValueError('Invalid trend lookbacks')
        for key, value in asdict(self).items():
            if isinstance(value, (float, int)) and not isinstance(value, bool) and not np.isfinite(value):
                raise ValueError(f'Nonfinite {key}')
        if not 0 < self.gross_cap <= 0.95 or not 0 < self.major_cap <= 0.4 or not 0 < self.other_cap <= 0.3:
            raise ValueError('Invalid exposure ceilings')
        if not 0 < self.target_vol <= 0.5 or not 0 <= self.short_scale <= 1 or not 0 < self.gate_min <= 1:
            raise ValueError('Invalid sizing parameters')
        if not 0 <= self.dd_soft < self.dd_hard or not 0 <= self.dd_floor <= 1:
            raise ValueError('Invalid drawdown governor')
        if type(self.sizing_hours) is not int or self.sizing_hours < 1 or not 0 <= self.sizing_phase < self.sizing_hours:
            raise ValueError('Invalid sizing schedule')
        if self.probe_total_usd < 10 or self.min_trade_usd < 1 or self.spacing < 2 or self.poll_seconds < 15:
            raise ValueError('Invalid execution budget')
        if self.maker_fee < 0.0005 or self.taker_fee < 0.001 or not 0 <= self.fixed_long <= 0.95:
            raise ValueError('Fees/exposure may not be understated')
        if pd.Timestamp(self.competition_end) <= pd.Timestamp(self.competition_start):
            raise ValueError('Invalid competition interval')

    @classmethod
    def load(cls, path='sigma_config.json'):
        obj = json.loads(Path(path).read_text())
        unknown = set(obj) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f'Unknown Sigma config fields: {sorted(unknown)}')
        for k in ('pairs', 'gate_lookbacks'):
            if k in obj:
                obj[k] = tuple(obj[k])
        return cls(**obj)

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@dataclass
class SigmaFeatures:
    index: pd.DatetimeIndex  # availability time: candle open + one hour
    pairs: tuple
    close: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    score: np.ndarray
    slow_vol: np.ndarray
    fast_vol: np.ndarray
    covariance: np.ndarray
    eligible: np.ndarray
    gates: dict


def prepare(bars: dict[str, pd.DataFrame], cfg: SigmaConfig) -> SigmaFeatures:
    if set(bars) != set(cfg.pairs):
        raise ValueError('Every fixed-universe asset needs complete history')
    panels = {}
    expected = None
    for pair in cfg.pairs:
        df = bars[pair].copy()
        validate_frame(df)
        times = df.timestamp.to_numpy(dtype=np.int64)
        if expected is None:
            expected = times
        elif not np.array_equal(times, expected):
            raise ValueError('Asset histories must align exactly; gaps cannot be forward-filled')
        panels[pair] = df
    breaks = np.flatnonzero(np.diff(expected) != HOUR)+1
    if len(breaks):
        bounds = [0, *breaks.tolist(), len(expected)]
        segments = [prepare({p:panels[p].iloc[a:b].reset_index(drop=True) for p in cfg.pairs}, cfg) for a,b in zip(bounds[:-1], bounds[1:])]
        index = segments[0].index.append([s.index for s in segments[1:]])
        matrix = lambda key: np.concatenate([getattr(s,key) for s in segments], axis=0)
        return SigmaFeatures(index,cfg.pairs,*[matrix(k) for k in ('close','open','high','low','score','slow_vol','fast_vol','covariance','eligible')],{k:np.concatenate([s.gates[k] for s in segments],axis=0) for k in segments[0].gates})
    index = pd.to_datetime(expected+HOUR, unit='ms', utc=True)
    close = pd.DataFrame({p: panels[p].close.to_numpy() for p in cfg.pairs}, index=index)
    logp = np.log(close)
    lr = logp.diff()
    slow, fast = ewm_vol(lr, cfg.vol_halflife), ewm_vol(lr, cfg.fast_vol_halflife)
    gate_parts = {}
    for lb in cfg.gate_lookbacks:
        z = (logp-logp.shift(lb))/(slow*np.sqrt(lb))
        g = np.sign(z).where(z.abs() > cfg.dead_band).ffill().where(z.notna()).fillna(0)
        gate_parts[str(lb)] = g.to_numpy()
    score = trend_gate_hysteresis(logp, slow, cfg.gate_lookbacks, cfg.dead_band).to_numpy()
    volume = pd.DataFrame({p: panels[p].quote_volume.to_numpy() for p in cfg.pairs}, index=index)
    elig = (volume.rolling(336, min_periods=168).sum()/14 >= cfg.min_daily_volume).to_numpy()
    elig[:max(cfg.warmup_hours, max(cfg.gate_lookbacks)), :] = False
    returns = close.pct_change(fill_method=None)
    cov = returns.rolling(cfg.cov_window, min_periods=48).cov().to_numpy().reshape(len(index), len(cfg.pairs), len(cfg.pairs))
    sd = np.sqrt(np.clip(np.diagonal(cov, axis1=1, axis2=2), 1e-12, None))
    corr = cov/(sd[:, :, None]*sd[:, None, :])
    n = len(cfg.pairs)
    rho = (corr.sum(axis=(1, 2))-n)/(n*(n-1))
    target = np.broadcast_to(rho[:, None, None], corr.shape).copy()
    target[:, np.arange(n), np.arange(n)] = 1
    cov = ((1-cfg.cov_shrink)*corr+cfg.cov_shrink*target)*sd[:, :, None]*sd[:, None, :]*8760
    values = lambda key: np.column_stack([panels[p][key].to_numpy() for p in cfg.pairs])
    return SigmaFeatures(index, cfg.pairs, close.to_numpy(), values('open'), values('high'), values('low'), score, slow.to_numpy(), fast.to_numpy(), cov, elig, gate_parts)


def load_features(cfg: SigmaConfig):
    return prepare({p: read_bars(cfg.data_dir, p) for p in cfg.pairs}, cfg)


def apply_caps(weights, cfg):
    caps = np.array([cfg.major_cap if p in {'BTC/USD', 'ETH/USD'} else cfg.other_cap for p in cfg.pairs])
    w = np.clip(np.asarray(weights, dtype=float), -caps, caps)
    gross = np.abs(w).sum()
    if gross > cfg.gross_cap:
        w *= cfg.gross_cap/gross
    return w


def target_weights(f: SigmaFeatures, i: int, current, nav: float, state: dict, cfg: SigmaConfig):
    """One closed-hour decision. State is serializable and never reset hourly."""
    current = np.asarray(current, float)
    if nav <= 0 or not np.isfinite(nav) or not np.isfinite(current).all():
        raise ValueError('Invalid portfolio state')
    hist = state.setdefault('nav_history', [])
    hist.append(float(nav))
    del hist[:-336]
    peak = max(hist)
    dd = max(0, 1-nav/peak)
    mult = 1 if dd <= cfg.dd_soft else cfg.dd_floor if dd >= cfg.dd_hard else 1-(dd-cfg.dd_soft)/(cfg.dd_hard-cfg.dd_soft)*(1-cfg.dd_floor)
    sigma = np.maximum(f.slow_vol[i], f.fast_vol[i]) if cfg.shock_guard else f.slow_vol[i]
    eligible = f.eligible[i] & np.isfinite(sigma) & (sigma > 0)
    inv = np.where(eligible, 1/np.maximum(sigma, 1e-12), 0)
    base = inv/inv.sum() if inv.sum() else np.zeros_like(inv)
    score = np.where(eligible & (np.abs(f.score[i])+1e-10 >= cfg.gate_min), f.score[i], 0)
    if not cfg.allow_shorts:
        score = np.maximum(score, 0)
    raw = score*base
    raw[raw < 0] *= cfg.short_scale
    risk_basket = raw if cfg.signed_covariance else base
    cov = f.covariance[i]
    port_vol = float(np.sqrt(max(0, risk_basket@cov@risk_basket))) if np.isfinite(cov).all() else float('nan')
    scale = min(1, cfg.target_vol/max(port_vol, 1e-12)) if np.isfinite(port_vol) else 0
    raw *= scale
    if cfg.fixed_long:
        initial = state.setdefault('initial_nav', float(nav))
        ret = nav/initial-1
        lock = state.get('fixed_lock', '')
        if not lock and (ret <= cfg.fixed_floor or ret >= cfg.fixed_goal):
            lock = 'floor' if ret <= cfg.fixed_floor else 'goal'
            state['fixed_lock'] = lock
        raw = np.zeros_like(base) if lock else base*cfg.fixed_long
    raw = apply_caps(raw*mult, cfg)
    changed_side = np.sign(raw) != np.sign(current)
    scheduled = int(f.index[i].timestamp()//3600) % cfg.sizing_hours == cfg.sizing_phase
    threshold = np.maximum(cfg.band_abs, cfg.band_rel*np.abs(raw))
    material = (np.abs(raw-current) > threshold) | changed_side
    material &= (np.abs(raw-current)*nav >= cfg.min_trade_usd) | (raw == 0)
    urgent_reduce = (np.abs(raw) < np.abs(current)) & ((mult < state.get('last_dd_multiplier', 1)) | (cfg.shock_guard & (f.fast_vol[i] > f.slow_vol[i]*1.5)))
    if not scheduled:
        material &= changed_side | urgent_reduce
    if state.get('fixed_lock'):
        material[:] = True
    result = apply_caps(np.where(material, raw, current), cfg)
    state['last_dd_multiplier'] = float(mult)
    state['gates'] = {k: np.asarray(v[i]).tolist() for k, v in f.gates.items()}
    return result, {'score': f.score[i].tolist(), 'gross': float(np.abs(result).sum()), 'net': float(result.sum()), 'drawdown_multiplier': float(mult), 'sizing_scheduled': bool(scheduled), 'annual_basket_vol': port_vol if np.isfinite(port_vol) else None}


def candidates():
    base = SigmaConfig()
    fast = replace(base, name='shorter-gates', gate_lookbacks=(126, 252, 540))
    return [
        base,
        fast,
        replace(fast, name='six-hour-sizing', sizing_hours=6),
        replace(fast, name='volatility-shock-guard', shock_guard=True),
        replace(fast, name='signed-covariance', signed_covariance=True),
        replace(fast, name='strong-gate', gate_min=2/3),
        replace(fast, name='combined-conservative', sizing_hours=6, shock_guard=True, target_vol=0.20),
        replace(fast, name='combined-signed', sizing_hours=6, shock_guard=True, signed_covariance=True, target_vol=0.20),
        replace(fast, name='current-upstream-fixed-long', fixed_long=0.90, dd_soft=0.06, dd_hard=0.15),
    ]
