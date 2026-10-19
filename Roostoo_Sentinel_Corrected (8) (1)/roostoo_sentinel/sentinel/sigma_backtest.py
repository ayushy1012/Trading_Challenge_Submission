"""Event-driven Sigma research. Historical fills are simulated, never API fills.

Orders decided after bar t closes execute no earlier than the next bar. There
is no same-close fill, invented queue advantage, rebate or zero-fee rounding
credit. Short proceeds are not spendable: twice their basis is reserved in the
signed-cash representation, equivalent to one-times collateral accounting.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .sigma import SigmaConfig, SigmaFeatures, target_weights


@dataclass
class SigmaResult:
    nav: pd.Series
    trades: pd.DataFrame
    diagnostics: pd.DataFrame
    summary: dict


def metrics(nav, initial=100000., fees=0, turnover=0, maker_notional=0, trades=0):
    x = np.asarray(nav, float)
    r = np.diff(x)/x[:-1]
    vol = float(r.std(ddof=1)) if len(r)>1 else 0
    downside = float(np.sqrt(np.mean(np.minimum(r, 0)**2))) if len(r) else 0
    return {'return': float(x[-1]/initial-1), 'sharpe': float(r.mean()/vol*np.sqrt(8760)) if vol else 0,
            'sortino': float(r.mean()/downside*np.sqrt(8760)) if downside else 0,
            'max_drawdown': float(np.max(1-x/np.maximum.accumulate(x))), 'fees_usd': float(fees),
            'turnover_nav': float(turnover/initial), 'maker_share': float(maker_notional/turnover) if turnover else 0,
            'trades': int(trades), 'hours': len(x)-1}


def run(f: SigmaFeatures, cfg: SigmaConfig, start, end, model='passive', initial=100000., competition_stops=False):
    if model not in {'passive', 'all_taker', 'double_cost', 'delayed'}:
        raise ValueError('Unknown execution model')
    ix = np.flatnonzero((f.index>=pd.Timestamp(start)) & (f.index<pd.Timestamp(end)))
    if len(ix)<2:
        raise ValueError('Insufficient evaluation interval')
    n=len(f.pairs)
    qty=np.zeros(n)
    basis=np.zeros(n)
    cash=float(initial)
    state={}
    pending={}
    market=[]
    rows=[]
    navrows=[(f.index[ix[0]]-pd.Timedelta(hours=1), cash)]
    diags=[]
    fees=turnover=maker_notional=0.
    scale_cost=2 if model=='double_cost' else 1
    slip=(5 if model=='delayed' else cfg.slippage_bps*scale_cost)/10000
    half_spread=np.array([0.005/85000, 0.005/2700, 0.005/120, 0.005/750, 0.00005/1.5])
    step=np.array([1e-5,1e-4,1e-3,1e-3,1e-1])
    price_tick=np.array([.01,.01,.01,.01,.0001])
    peak=float(initial)
    day=''
    day_start=float(initial)
    permanent=''
    daily=''
    daily_losses=0
    pair_realized=np.zeros(n)
    halts=[]

    def short_basis():
        return float(np.sum(np.where(qty<0,-qty*basis,0)))

    def liquidation(px):
        return cash+float(qty@px)-float((np.abs(qty)*px*(cfg.taker_fee*scale_cost+half_spread+slip)).sum())

    def fill(j, dq, px, role, t, reason):
        nonlocal cash,fees,turnover,maker_notional,daily_losses
        if abs(dq)<step[j]/2:
            return
        # A single fill cannot flip sign. Plan the reducing leg first.
        q0=qty[j]
        if q0*dq<0:
            dq=math.copysign(min(abs(dq),abs(q0)),dq)
        opening=q0==0 or q0*dq>0
        fee_rate=(cfg.maker_fee if role=='MAKER' else cfg.taker_fee)*scale_cost
        if opening:
            free=cash-2*short_basis()
            affordable=max(0,free)/(px*(1+fee_rate))
            mark=f.open[i] if reason!='terminal_liquidation' else f.close[i]
            eq=liquidation(mark)
            gross=float((np.abs(qty)*mark).sum())
            cap=cfg.major_cap if f.pairs[j] in {'BTC/USD','ETH/USD'} else cfg.other_cap
            budget=max(0,min(cfg.gross_cap*eq-gross,cap*eq-abs(qty[j])*mark[j]))
            exposure_allowance=budget/(px*(1+fee_rate+2*slip+2*half_spread[j]))
            dq=math.copysign(min(abs(dq),affordable,exposure_allowance),dq)
        dq=math.copysign(np.floor((abs(dq)+1e-10)/step[j])*step[j],dq)
        if abs(dq)<step[j]/2:
            return
        amount=abs(dq)*px
        fee=amount*fee_rate
        cash-=dq*px+fee
        if opening:
            basis[j]=(abs(q0)*basis[j]+abs(dq)*px)/(abs(q0)+abs(dq))
            pair_realized[j]-=fee
        else:
            realized=abs(dq)*np.sign(q0)*(px-basis[j])-fee
            pair_realized[j]+=realized
        qty[j]+=dq
        if abs(qty[j])<step[j]/2:
            qty[j]=basis[j]=0
            daily_losses=daily_losses+1 if pair_realized[j]<0 else 0
            pair_realized[j]=0
        fees+=fee
        turnover+=amount
        maker_notional+=amount if role=='MAKER' else 0
        rows.append({'timestamp':t.isoformat(),'pair':f.pairs[j],'quantity_delta':float(dq),'price':float(px),'role':role,'fee_usd':float(fee),'reason':reason,'source':'historical_simulation','model':model})

    for k,i in enumerate(ix):
        t=f.index[i]
        if i>0 and f.index[i]-f.index[i-1]!=pd.Timedelta(hours=1):
            # No model fills in an unobserved hour. Cancel stale plans and
            # reduce known inventory at the first observable opening quote.
            pending={}
            market=[(j,-qty[j],'data_gap_reduction') for j in range(n) if qty[j]!=0]
        half_spread=price_tick/(2*f.open[i])
        hktday=t.tz_convert('Asia/Hong_Kong').date().isoformat()
        if hktday!=day:
            day=hktday
            day_start=liquidation(f.open[i])
            daily=''
            daily_losses=0
        # Market orders were planned at the preceding close.
        queued=market
        market=[]
        for j,dq,reason in sorted(queued,key=lambda o:0 if qty[o[0]]*o[1]<0 else 1):
            q0=qty[j]
            signed=1 if dq>0 else -1
            px=f.open[i,j]*(1+signed*(half_spread[j]+slip))
            fill(j,dq,px,'TAKER',t-pd.Timedelta(hours=1),reason)
        # Passive orders must cross through an executable-side proxy, not touch.
        for j,o in list(pending.items()):
            through=o['limit']*(1-o['side']*cfg.passive_through_bps/10000)
            hit=f.low[i,j]<=through if o['side']>0 else f.high[i,j]>=through
            if hit:
                fill(j,o['dq'],o['limit'],'MAKER',t,'trade_through_proxy')
                del pending[j]
            else:
                o['age']+=1
        px=f.close[i]
        nav=liquidation(px)
        peak=max(peak,nav)
        reason=''
        if competition_stops:
            if nav<=initial*(1-cfg.total_loss):permanent=permanent or 'total_loss'
            if nav<=peak*(1-cfg.max_drawdown):permanent=permanent or 'max_drawdown'
            if nav>=initial*(1+cfg.total_profit):permanent=permanent or 'total_profit'
            if nav<=day_start*(1-cfg.daily_loss):daily=daily or 'daily_loss'
            if nav>=day_start*(1+cfg.daily_profit):daily=daily or 'daily_profit'
            if daily_losses>=cfg.max_consecutive_losses:daily=daily or 'consecutive_losses'
            reason=permanent or daily
        current=qty*px/max(nav,1e-9)
        w,d=target_weights(f,i,current,nav,state,cfg)
        if reason:
            w[:]=0
            if not halts or halts[-1]['reason']!=reason or halts[-1]['day']!=day:
                halts.append({'timestamp':t.isoformat(),'reason':reason,'day':day})
        # Reconcile target at each close; cancel stale opposite/budgeted limits.
        new_pending={}
        for j in range(n):
            target=w[j]*nav/px[j]
            q0=qty[j]
            if target*q0<0:
                dq=-q0
                reducing=True
            else:
                dq=target-q0
                reducing=q0*dq<0
            amount=abs(dq)*px[j]
            if amount<cfg.min_trade_usd and not (target==0 and q0!=0):
                continue
            dq=math.copysign(min(abs(dq),cfg.max_order_fraction*nav/px[j]),dq)
            if abs(dq)<step[j]/2:continue
            o=pending.get(j)
            risk_exit=bool(reason or target==0 or target*q0<0)
            short_leg=q0<0 or target<0
            fallback=o is not None and o['age']>=max(1,cfg.maker_patience_seconds//3600)
            if model!='passive' or risk_exit or short_leg or fallback:
                market.append((j,dq,'risk_exit' if risk_exit else 'short' if short_leg else 'passive_timeout' if fallback else 'next_open'))
            else:
                side=1 if dq>0 else -1
                limit=px[j]*(1-side*half_spread[j])
                limit=np.floor(limit/price_tick[j])*price_tick[j] if side>0 else np.ceil(limit/price_tick[j])*price_tick[j]
                new_pending[j]={'dq':dq,'limit':limit,'side':side,'age':0 if o is None else o['age']}
        pending=new_pending
        navrows.append((t,nav))
        diags.append({'timestamp':t,'gross':float(np.abs(current).sum()),'cash_free_proxy':float(cash-2*short_basis()),'halt':reason,**d})
    # Explicit terminal liquidation prevents uncharged exit costs inflating return.
    i=ix[-1]
    for j in range(n):
        if qty[j]!=0:
            dq=-qty[j]
            side=1 if dq>0 else -1
            fill(j,dq,f.close[i,j]*(1+side*(half_spread[j]+slip)),'TAKER',f.index[i],'terminal_liquidation')
    navrows[-1]=(f.index[i],cash)
    nav=pd.Series([v for _,v in navrows],index=pd.DatetimeIndex([t for t,_ in navrows]),name='net_liquidation_equity')
    summary=metrics(nav,initial,fees,turnover,maker_notional,len(rows))
    summary.update({'model':model,'config_hash':cfg.fingerprint,'competition_stops':competition_stops,'halt_events':halts,'initial_usd':initial,'start':str(nav.index[0]),'end':str(nav.index[-1]),'source':'historical_simulation','hourly_stop_resolution':True})
    return SigmaResult(nav,pd.DataFrame(rows),pd.DataFrame(diags),summary)


def export(result: SigmaResult, directory):
    import json
    root=Path(directory)
    root.mkdir(parents=True,exist_ok=True)
    result.nav.to_csv(root/'nav.csv')
    result.trades.to_csv(root/'trade_logs.csv',index=False)
    result.diagnostics.to_csv(root/'decisions.csv',index=False)
    (root/'summary.json').write_text(json.dumps(result.summary,indent=2,allow_nan=False))
