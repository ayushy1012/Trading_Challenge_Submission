"""Durable, bounded Roostoo adapter for the Sigma allocation.

Shadow is the default. Paper-exchange mode uses real signed mock API orders.
One unresolved write pauses all other writes; only journaled orders may be
canceled. Targets use closed hourly bars; risk is checked every polling cycle.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal,ROUND_UP
import json
import math
from pathlib import Path
import signal
import time

import numpy as np
import pandas as pd

from .account import bind_account
from .api import ApiError,AmbiguousWrite,Roostoo,decimal_string
from .data import HOUR,LiveData
from .runner import InstanceLock,code_version
from .sigma import SigmaConfig,prepare,target_weights,apply_caps
from .state import Store


def normalize_order(detail, before_coin, current_coin):
    """Reject impossible fills; recognize the observed zero-execution echo."""
    row=dict(detail)
    requested=float(row['Quantity'])
    filled=float(row.get('FilledQuantity',0))
    avg=float(row.get('FilledAverPrice',0))
    fee=float(row.get('CommissionChargeValue',0))
    zeros=all(k in row and float(row[k])==0 for k in ('CoinChange','UnitChange','CommissionChargeValue','FilledAverPrice'))
    if filled and avg==0 and zeros and abs(filled-requested)<1e-10 and abs(current_coin-before_coin)<1e-10:
        row['FilledQuantity']=filled=0.
    if not all(math.isfinite(v) for v in (requested,filled,avg,fee)) or not 0<=filled<=requested+1e-8 or (filled and avg<=0):
        raise ApiError('Impossible exchange fill; writes paused')
    return row


def account_snapshot(accounts,shorts,ticker,cfg):
    spot=accounts['spot'];margin=accounts['margin']
    if any(sum(v.values()) for v in margin.values()):
        raise ApiError('Funded MarginWallet accounting needs separate verification; new writes paused')
    usd=spot['USD'];nav=usd['Free']+usd['Lock']
    values=np.zeros(len(cfg.pairs));quantities=np.zeros(len(cfg.pairs))
    collateral=sum(float(s['Collateral']) for s in shorts)
    if collateral>usd['Lock']+.04:
        raise ApiError('Collateral is not represented in SpotWallet USD Lock; accounting unverified')
    for j,pair in enumerate(cfg.pairs):
        q=ticker['Data'][pair]
        bid,ask=float(q['MaxBid']),float(q['MinAsk'])
        if not 0<bid<=ask or not math.isfinite(bid+ask):raise ApiError('Invalid executable quote')
        coin=spot.get(pair.split('/')[0],{'Free':0,'Lock':0})
        long=float(coin['Free'])+float(coin['Lock'])
        short=next((s for s in shorts if s['Pair']==pair),None)
        if long and short:raise ApiError('Simultaneous long and short on one pair is outside this strategy')
        qty=long if not short else -float(short['ShortQty'])
        quantities[j]=qty;values[j]=qty*(bid if qty>=0 else ask)
        nav+=long*bid*(1-cfg.taker_fee)
        if short:
            pnl=max(-float(short['Collateral']),float(short['ShortQty'])*(float(short['EntryPrice'])-ask))
            nav+=pnl-float(short['ShortQty'])*ask*cfg.taker_fee
    for c,v in spot.items():
        if c!='USD' and c+'/USD' not in cfg.pairs and sum(v.values()):raise ApiError('Unmanaged asset outside fixed universe')
    if not math.isfinite(nav) or nav<=0:raise ApiError('Invalid account equity')
    return float(nav),float(usd['Free']),quantities,values


def risk_reason(nav,now,risk,cfg):
    day=pd.Timestamp(now,unit='ms',tz='UTC').tz_convert('Asia/Hong_Kong').date().isoformat()
    if risk.get('day')!=day:
        risk.update(day=day,day_start=nav,daily_halt='',consecutive_losses=0)
    risk.setdefault('initial',nav);risk.setdefault('peak',nav);risk.setdefault('permanent_halt','')
    risk['peak']=max(risk['peak'],nav)
    permanent=''
    if nav<=risk['initial']*(1-cfg.total_loss):permanent='total_loss'
    elif nav<=risk['peak']*(1-cfg.max_drawdown):permanent='max_drawdown'
    elif nav>=risk['initial']*(1+cfg.total_profit):permanent='total_profit'
    risk['permanent_halt']=risk['permanent_halt'] or permanent
    daily=''
    if nav<=risk['day_start']*(1-cfg.daily_loss):daily='daily_loss'
    elif nav>=risk['day_start']*(1+cfg.daily_profit):daily='daily_profit'
    elif risk.get('consecutive_losses',0)>=cfg.max_consecutive_losses:daily='consecutive_losses'
    risk['daily_halt']=risk.get('daily_halt','') or daily
    return risk['permanent_halt'] or risk['daily_halt']


class SigmaRunner:
    def __init__(self,cfg,mode='shadow',max_minutes=None,portfolio_cap=None,prefer_passive=False):
        if mode not in {'shadow','paper-exchange'}:raise ValueError('Unknown Sigma mode')
        if max_minutes is not None and not 0<max_minutes<=1440:raise ValueError('Invalid bounded duration')
        self.cfg,self.mode=cfg,mode
        self.prefer_passive=bool(prefer_passive)
        self.cap=cfg.probe_total_usd if portfolio_cap is None else float(portfolio_cap)
        if not math.isfinite(self.cap) or self.cap<10:raise ValueError('Invalid total portfolio cap')
        self.lock=InstanceLock(Path(cfg.runtime_dir)/'bot.lock')
        self.store=Store(Path(cfg.runtime_dir)/('sigma_'+mode.replace('-','_')+'.sqlite3'))
        self.api=Roostoo(cfg.base_url,cfg.spacing)
        self.api.sync_time()
        if mode=='paper-exchange':bind_account(self.api,self.store)
        self.meta=self.api.exchange_info()['TradePairs']
        for p in cfg.pairs:
            if self.meta.get(p,{}).get('CanTrade') is not True:raise ApiError('Fixed-universe pair unavailable')
        self.version=code_version()
        self.feed=LiveData(cfg.data_base_url,cfg.data_dir,cfg.spacing)
        self.pool=ThreadPoolExecutor(max_workers=1)
        self.future=None;self.features=None;self.feed_hour=None
        self.policy=self.store.get('sigma_policy',{})
        self.risk=self.store.get('sigma_risk',{})
        self.owned=self.store.get('sigma_owned',[0.]*len(cfg.pairs))
        self.targets=self.store.get('sigma_targets',[0.]*len(cfg.pairs))
        self.last_hour=self.store.get('sigma_last_hour')
        self.stop_requested=False
        self.last_report=0.
        session=self.store.get('sigma_session',{})
        if session.get('complete'):
            session={}
        if max_minutes is not None and not session:
            session={'deadline_ms':self.api.now_ms()+int(max_minutes*60000),'stopping':False,'complete':False}
        self.session=session
        self.store.set('sigma_session',session)
        self.store.event('sigma_start',{'mode':mode,'config_hash':cfg.fingerprint,'commit':self.version,'portfolio_cap_usd':self.cap,'deadline_ms':session.get('deadline_ms'),'prefer_passive':self.prefer_passive})

    def refresh(self,now):
        hour=now//HOUR
        if self.future is not None and self.future.done():
            try:
                self.features=self.future.result()
            except Exception as e:
                self.features=None;self.feed_hour=None
                self.store.event('sigma_data_pause',{'error':f'{type(e).__name__}: {e}'})
            self.future=None
        if self.future is None and self.feed_hour!=hour:
            self.feed_hour=hour
            def update():
                return prepare({p:self.feed.update(p,now,65) for p in self.cfg.pairs},self.cfg)
            self.future=self.pool.submit(update)

    def snapshot(self,ticker,check_orders=True):
        if self.mode=='shadow':
            return 100000.,100000.,np.zeros(len(self.cfg.pairs)),np.zeros(len(self.cfg.pairs)),{},[]
        accounts=self.api.balance_accounts();shorts=self.api.short_positions()
        nav,free,qty,values=account_snapshot(accounts,shorts,ticker,self.cfg)
        if check_orders:
            pending=self.api.query_orders(pending_only=True)
            known={str((r['response'] or {}).get('OrderDetail',{}).get('OrderID')) for r in self.store.unfinished()}
            if any(str(o['OrderID']) not in known for o in pending):raise ApiError('Unmanaged pending order; preserve previous bot/ledger')
        return nav,free,qty,values,accounts['spot'],shorts

    def reconcile(self,ticker):
        if self.mode=='shadow':return True
        for intent in self.store.unfinished():
            p=intent['payload'];r=intent['response'] or {};j=self.cfg.pairs.index(p['pair'])
            wallet=self.api.balance();shorts=self.api.short_positions()
            spot=wallet.get(p['pair'].split('/')[0],{'Free':0,'Lock':0})
            actual_long=spot['Free']+spot['Lock']
            actual_short=next((s for s in shorts if s['Pair']==p['pair']),None)
            actual=-float(actual_short['ShortQty']) if actual_short else actual_long
            if p['side'] in {'SHORT_OPEN','SHORT_CLOSE'}:
                if not r:
                    # A uniquely changed, owned short quantity reconciles a lost
                    # response; an unchanged book cannot prove rejection.
                    changed=actual-p['before_qty']
                    expected=-float(p['quantity']) if p['side']=='SHORT_OPEN' else float(p['quantity'])
                    if abs(changed-expected)>2*10**-int(self.meta[p['pair']]['AmountPrecision']):
                        raise ApiError('Ambiguous short write; no resubmission')
                    self.risk['permanent_halt']='short_execution_fee_unverified'
                    self.store.set('sigma_risk',self.risk)
                elif p['side']=='SHORT_OPEN' and r.get('Status')!='OPEN':raise ApiError('Short open was not confirmed')
                elif p['side']=='SHORT_CLOSE' and 'ClosedQty' not in r:raise ApiError('Short close was not confirmed')
                if r:
                    observed_fee=float(r.get('OpenFee',0) if p['side']=='SHORT_OPEN' else r.get('CloseFee',0))
                    price=float(r.get('EntryPrice',0) if p['side']=='SHORT_OPEN' else r.get('ClosePrice',0))
                    if price<=0 or observed_fee>abs(actual-p['before_qty'])*price*self.cfg.taker_fee+.02:
                        self.risk['permanent_halt']='unexpected_short_fee';self.store.set('sigma_risk',self.risk)
                self.owned[j]=actual
                fill={'pair':p['pair'],'side':p['side'],'quantity':abs(actual-p['before_qty']),'response':r,'mode':self.mode,'purpose':p['reason']}
                self._finish(intent,fill,actual)
                continue
            oid=r.get('OrderDetail',{}).get('OrderID')
            if oid is None:
                matches=self.api.query_orders(limit=100)
                matches=[o for o in matches if o.get('Pair')==p['pair'] and o.get('Side')==p['side'] and o.get('Type')==p['type'] and abs(int(o.get('CreateTimestamp',0))-p['request_timestamp'])<60000 and abs(float(o.get('Quantity',0))-float(p['quantity']))<10**-int(self.meta[p['pair']]['AmountPrecision']) and (p['type']=='MARKET' or abs(float(o.get('Price',0))-float(p['price']))<10**-int(self.meta[p['pair']]['PricePrecision']))]
                if len(matches)!=1:raise ApiError('Ambiguous spot write; no resubmission')
                r={'OrderDetail':matches[0]};oid=matches[0]['OrderID']
                self.store.update_intent(intent['id'],'acknowledged',r)
            rows=self.api.query_orders(order_id=oid)
            if len(rows)!=1:raise ApiError('Owned order missing')
            detail=normalize_order(rows[0],p['before_qty'],actual_long)
            filled=float(detail['FilledQuantity'])
            expected=p['before_qty']+(1 if p['side']=='BUY' else -1)*filled
            if abs(expected-actual)>max(2*10**-int(self.meta[p['pair']]['AmountPrecision']),filled*.0011):
                raise ApiError('Order and wallet holdings disagree; new writes paused')
            self.owned[j]=actual
            self.store.set('sigma_owned',self.owned)
            if detail['Status'] in {'PENDING','PARTIALLY_FILLED'}:
                expired=self.api.now_ms()-p['request_timestamp']>=self.cfg.maker_patience_seconds*1000
                sign_changed=np.sign(self.targets[j])*np.sign(float(p['quantity'])*(1 if p['side']=='BUY' else -1))<0
                if expired or self.session.get('stopping') or self.stop_requested or self.risk.get('permanent_halt') or self.risk.get('daily_halt') or sign_changed:
                    try:self.api.cancel_order(oid)
                    except (AmbiguousWrite,ApiError):pass
                    self.store.set('sigma_fallback_'+p['pair'],bool(expired))
                    self.store.event('sigma_cancel_requested',{'owned_order_id':oid,'reason':'expired_or_risk_reduction'})
                return False
            if detail['Status'] not in {'FILLED','CANCELED','CANCELLED','REJECTED'}:raise ApiError('Unexpected order state')
            if filled and abs(float(detail.get('CommissionPercent',self.cfg.taker_fee)))>self.cfg.taker_fee+1e-8:
                self.risk['permanent_halt']='unexpected_fee';self.store.set('sigma_risk',self.risk)
            self._finish(intent,{'pair':p['pair'],'side':p['side'],'quantity':filled,'order':detail,'mode':self.mode,'purpose':p['reason']},actual)
        return True

    def _finish(self,intent,fill,actual):
        j=self.cfg.pairs.index(intent['payload']['pair']);self.owned[j]=actual
        p=intent['payload'];q0=float(p['before_qty']);delta=actual-q0
        book=self.store.get('sigma_cost_basis',{})
        previous=book.get(p['pair'],{'basis':0.,'realized':0.})
        detail=fill.get('order',{}) or fill.get('response',{})
        price=float(detail.get('FilledAverPrice') or detail.get('ClosePrice') or detail.get('EntryPrice') or 0)
        fee=float(detail.get('CommissionChargeValue') or detail.get('CloseFee') or detail.get('OpenFee') or 0)
        if detail.get('CommissionCoin')==p['pair'].split('/')[0]:fee*=price
        previous['realized']-=fee
        if delta and price:
            if q0==0 or q0*delta>0:
                previous['basis']=price if p.get('side')=='SHORT_OPEN' else (abs(q0)*previous['basis']+abs(delta)*price)/(abs(q0)+abs(delta))
            else:
                previous['realized']+=float(detail['RealizedPNL']) if p.get('side')=='SHORT_CLOSE' and 'RealizedPNL' in detail else min(abs(q0),abs(delta))*np.sign(q0)*(price-previous['basis'])
        if fill['quantity']>0 and q0!=0 and q0*delta<0 and abs(actual)<10**-int(self.meta[p['pair']]['AmountPrecision']):
            self.risk['consecutive_losses']=self.risk.get('consecutive_losses',0)+1 if previous['realized']<0 else 0
            fill['closed_or_dust_position_net_pnl']=previous['realized']
            previous={'basis':previous['basis'] if actual else 0.,'realized':0.}
        book[p['pair']]=previous
        with self.store.db:
            self.store.db.execute("INSERT INTO kv VALUES('sigma_owned',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(self.owned),))
            self.store.db.execute("INSERT INTO kv VALUES('sigma_cost_basis',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(book),))
            self.store.db.execute("INSERT INTO kv VALUES('sigma_risk',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(self.risk),))
            if fill['quantity']>0:self.store.db.execute('INSERT OR IGNORE INTO fills VALUES(?,?,?)',(intent['id'],self.api.now_ms(),json.dumps(fill)))
            self.store.db.execute("UPDATE intents SET status='done' WHERE id=?",(intent['id'],))
        self.store.event('sigma_order_terminal',fill)

    def submit(self,pair,side,quantity,price,reason,ticker,qty,free,nav):
        # Last possible entry check: API reads and reconciliation can consume
        # the entire test budget. Cleanup writes remain reduce-only.
        if self.session.get('deadline_ms') and self.api.now_ms()>=self.session['deadline_ms']:
            self.session['stopping']=True
            self.store.set('sigma_session',self.session)
        if side in {'BUY','SHORT_OPEN'} and (self.session.get('stopping') or self.stop_requested):
            return
        if self.store.unfinished():return
        j=self.cfg.pairs.index(pair);meta=self.meta[pair]
        qstr=decimal_string(quantity,int(meta['AmountPrecision']))
        if float(qstr)<=0:return
        quote=ticker['Data'][pair];ref=float(quote['MinAsk'] if side in {'BUY','SHORT_CLOSE'} else quote['MaxBid'])
        if float(qstr)*ref<=max(float(meta['MiniOrder']),1):return
        typ='LIMIT' if price is not None and side in {'BUY','SELL'} else 'MARKET'
        pstr=None if price is None else format(Decimal(str(price)).quantize(Decimal(1).scaleb(-int(meta['PricePrecision'])),rounding=ROUND_UP if side=='SELL' else 'ROUND_DOWN'),'f')
        p={'pair':pair,'side':side,'quantity':qstr,'type':typ,'price':pstr,'before_qty':float(qty[j]),'before_usd':float(free),'request_timestamp':self.api.now_ms(),'mode':self.mode,'reason':reason,'config_hash':self.cfg.fingerprint,'commit':self.version}
        key=self.store.intent(f"sigma|{pair}|{side}|{p['request_timestamp']}",p,p['request_timestamp'])
        if key is None:return
        self.store.event('sigma_order_intent',{'intent':key,**p})
        try:
            if side=='SHORT_OPEN':r=self.api.short_open(pair,decimal_string(min(quantity*ref,free/(1+self.cfg.taker_fee)),2))
            elif side=='SHORT_CLOSE':r=self.api.short_close(pair,qstr)
            elif typ=='LIMIT':r=self.api.place_limit(pair,side,qstr,pstr)
            else:r=self.api.place(pair,side,qstr)
        except AmbiguousWrite:
            self.store.update_intent(key,'unknown');return
        except ApiError as e:
            self.store.update_intent(key,'rejected',{'error':str(e)})
            if side=='SHORT_OPEN' and 'does not allow' in str(e):
                self.store.set('sigma_short_permission',False)
                self.store.event('sigma_short_disabled',{'reason':str(e),'action':'future targets exclude shorts'})
            return
        self.store.update_intent(key,'acknowledged',r)

    def cycle(self):
        now=self.api.now_ms()
        if time.monotonic()-self.api.last_sync>1800:now=self.api.sync_time()
        self.refresh(now)
        ticker=self.api.ticker()
        if abs(int(ticker.get('ServerTime',0))-now)>120000:raise ApiError('Stale Roostoo ticker')
        if self.session.get('deadline_ms') and now>=self.session['deadline_ms']:self.session['stopping']=True
        if self.stop_requested:self.session['stopping']=True
        self.store.set('sigma_session',self.session)
        # Check risk with known partial ownership before deciding whether a
        # resting order may remain. Ownership is audited after reconciliation.
        nav,free,qty,values,wallet,shorts=self.snapshot(ticker,check_orders=False)
        halt=risk_reason(nav,now,self.risk,self.cfg)
        self.store.set('sigma_risk',self.risk)
        if time.monotonic()-self.last_report>=60:
            self.report(nav,qty,halt);self.last_report=time.monotonic()
        end=int(pd.Timestamp(self.cfg.competition_end).timestamp()*1000)
        finishing=now>=end-300000
        if finishing:self.session['stopping']=True
        if not self.reconcile(ticker):return False
        if self.mode=='paper-exchange':
            nav,free,qty,values,wallet,shorts=self.snapshot(ticker)
            halt=risk_reason(nav,self.api.now_ms(),self.risk,self.cfg)
            self.store.set('sigma_risk',self.risk)
            for j,p in enumerate(self.cfg.pairs):
                tolerance=max(1e-8,abs(self.owned[j])*1e-8)
                if abs(qty[j]-self.owned[j])>tolerance:raise ApiError('Unmanaged holding or conflicting bot; no mutations')
        if self.session.get('deadline_ms') and self.api.now_ms()>=self.session['deadline_ms']:
            self.session['stopping']=True;self.store.set('sigma_session',self.session)
        if self.features is not None:
            f=self.features;i=len(f.index)-1
            hour=int(f.index[i].timestamp()*1000)//HOUR
            fresh=now-int(f.index[i].timestamp()*1000)<2*HOUR
            if fresh and hour!=self.last_hour:
                # Carry hysteresis from the last persisted decision when a
                # rolling live data window no longer contains the old crossing.
                if self.last_hour is not None and self.policy.get('gates'):
                    new=np.flatnonzero((f.index.view('int64')//10**9//3600)>self.last_hour)
                    for lb in self.cfg.gate_lookbacks:
                        previous=np.asarray(self.policy['gates'].get(str(lb),[0.]*len(self.cfg.pairs)))
                        for row in new:
                            if row<lb:continue
                            z=np.log(f.close[row]/f.close[row-lb])/(f.slow_vol[row]*np.sqrt(lb))
                            previous=np.where(np.abs(z)>self.cfg.dead_band,np.sign(z),previous)
                            f.gates[str(lb)][row]=previous
                    f.score[new]=sum(f.gates[str(lb)][new] for lb in self.cfg.gate_lookbacks)/len(self.cfg.gate_lookbacks)
                c=self.cfg if self.store.get('sigma_short_permission',True) else replace(self.cfg,allow_shorts=False)
                w,d=target_weights(f,i,values/nav,nav,self.policy,c)
                total=float(np.abs(w).sum())
                if total*nav>self.cap:w*=self.cap/(total*nav)
                d['unscaled_target_gross']=total;d['gross']=float(np.abs(w).sum())
                self.targets=w.tolist();self.last_hour=hour
                self.store.set('sigma_policy',self.policy);self.store.set('sigma_targets',self.targets);self.store.set('sigma_last_hour',hour)
                self.store.event('sigma_target',{'mode':self.mode,'config_hash':self.cfg.fingerprint,'closed_bar_utc':f.index[i].isoformat(),'weights':self.targets,'equity':nav,**d})
            elif not fresh:
                halt=halt or 'stale_external_data'
        elif not halt and not self.session.get('stopping'):
            self.store.event('sigma_wait',{'reason':'loading_closed_hourly_history','mode':self.mode});return False
        if halt or self.session.get('stopping'):self.targets=[0.]*len(self.cfg.pairs)
        target=np.asarray(self.targets)*nav
        if time.monotonic()-self.last_report>=60:
            self.report(nav,qty,halt);self.last_report=time.monotonic()
        if self.mode=='shadow':
            self.store.event('sigma_shadow_plan',{'weights':self.targets,'equity_reference':nav,'account_writes':0,'profitability_evidence':False})
            return bool(self.session.get('stopping'))
        # Risk reductions precede all increases; at most one unresolved order.
        legs=[]
        for j,pair in enumerate(self.cfg.pairs):
            current=values[j];desired=target[j]
            reduce=current!=0 and (np.sign(current)!=np.sign(desired) or abs(desired)<abs(current))
            ref=float(ticker['Data'][pair]['MinAsk'] if current<0 else ticker['Data'][pair]['MaxBid'])
            delta=abs(current) if current*desired<0 else abs(desired-current)
            minimum=min(self.cfg.min_trade_usd,max(5,self.cap*.05))
            if delta<minimum and not (reduce and desired==0):continue
            amount=min(delta,self.cfg.max_order_fraction*nav)
            if not reduce:
                gross=float(np.abs(values).sum())
                cap=self.cfg.major_cap if pair in {'BTC/USD','ETH/USD'} else self.cfg.other_cap
                amount=min(amount,free/(1+self.cfg.taker_fee),max(0,min(self.cfg.gross_cap*nav,self.cap)-gross),max(0,cap*nav-abs(current)))
            if amount<=float(self.meta[pair]['MiniOrder']):continue
            side='SHORT_CLOSE' if current<0 and reduce else 'SELL' if reduce else 'SHORT_OPEN' if desired<0 else 'BUY'
            q=amount/ref
            if reduce:q=min(q,abs(qty[j]))
            urgent=bool(halt or self.session.get('stopping') or desired==0 or current*desired<0)
            fallback=self.store.get('sigma_fallback_'+pair,False)
            price=None if not self.prefer_passive or urgent or fallback or side.startswith('SHORT') else float(ticker['Data'][pair]['MaxBid' if side=='BUY' else 'MinAsk'])
            legs.append((0 if reduce else 1,j,pair,side,q,price,'risk_reduction' if urgent else 'allocation'))
        if legs:
            _,j,pair,side,q,price,reason=sorted(legs,key=lambda x:(x[0],-abs(target[x[1]]-values[x[1]])))[0]
            self.submit(pair,side,q,price,reason,ticker,qty,free,nav)
            if price is None:self.store.set('sigma_fallback_'+pair,False)
            return False
        dust_limits=np.array([max(float(self.meta[p]['MiniOrder']),2*10**-int(self.meta[p]['AmountPrecision'])*float(ticker['Data'][p]['MaxBid'])) for p in self.cfg.pairs])
        clean=bool((np.abs(values)<dust_limits).all() and not shorts and not self.store.unfinished())
        if self.session.get('stopping') and clean:
            self.report(nav,qty,halt)
            self.session['complete']=True;self.store.set('sigma_session',self.session);return True
        self.report(nav,qty,halt)
        return False

    def report(self,nav,qty,halt):
        root=Path(self.cfg.runtime_dir)/'reports'/('sigma-'+self.mode);root.mkdir(parents=True,exist_ok=True)
        status={'mode':self.mode,'config_hash':self.cfg.fingerprint,'equity':nav,'return':nav/self.risk.get('initial',nav)-1,'holdings':dict(zip(self.cfg.pairs,qty.tolist())),'targets':dict(zip(self.cfg.pairs,self.targets)),'risk':self.risk,'halt':halt,'session':self.session,'portfolio_cap_usd':self.cap,'prefer_passive':self.prefer_passive,'fill_records':len(self.store.fills()),'unresolved_orders':len(self.store.unfinished())}
        (root/'status.json').write_text(json.dumps(status,indent=2,allow_nan=False))
        pd.DataFrame(self.store.fills()).to_csv(root/'trade_logs.csv',index=False)
        (root/'summary.txt').write_text(f"Sigma {self.mode}\nEquity: {nav:.2f}\nReturn: {status['return']:.6%}\nHalt: {halt or 'none'}\nPortfolio cap: ${self.cap:.2f}\nConfirmed records: {status['fill_records']}\nHistorical profitability is separate from observed account returns.\n")

    def run(self,once=False):
        def stop(*_):self.stop_requested=True
        previous={s:signal.signal(s,stop) for s in (signal.SIGINT,signal.SIGTERM)}
        try:
            if once:
                self.refresh(self.api.now_ms())
                if self.future is not None:
                    self.features=self.future.result();self.future=None
            while True:
                try:
                    done=self.cycle()
                    if done or once:break
                except (ApiError,ValueError) as e:
                    self.store.event('sigma_pause',{'error':str(e),'action':'no blind resubmission; risk journal preserved'})
                    if once:raise
                time.sleep(self.cfg.poll_seconds)
        finally:
            self.pool.shutdown(wait=True)
            self.store.close();self.lock.close()
            for s,h in previous.items():signal.signal(s,h)


def diagnose(cfg,mode='paper-exchange',remote=False):
    store=Store(Path(cfg.runtime_dir)/('sigma_'+mode.replace('-','_')+'.sqlite3'))
    try:
        report={'mode':mode,'config_hash':cfg.fingerprint,'risk':store.get('sigma_risk',{}),'owned_quantities':store.get('sigma_owned',[]),'targets':store.get('sigma_targets',[]),'session':store.get('sigma_session',{}),'unresolved':store.unfinished(),'fill_records':store.fills(),'account_mutations':0}
        if remote:
            api=Roostoo(cfg.base_url,cfg.spacing);api.sync_time()
            accounts=api.balance_accounts();shorts=api.short_positions();ticker=api.ticker()
            nav,free,qty,values=account_snapshot(accounts,shorts,ticker,cfg)
            report['remote']={'equity_after_estimated_liquidation':nav,'free_usd':free,'wallet_schema':accounts['schema'],'pending_orders':api.query_orders(pending_only=True),'short_positions':shorts,'holdings':dict(zip(cfg.pairs,qty.tolist())),'gross_usd':float(np.abs(values).sum())}
        return report
    finally:store.close()
