"""Checksum-verified Sigma history. Explicit dates; completed days only."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import sys
import zipfile

import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sentinel.data import RAW_COLUMNS, download_history, get_bytes, normalize_frame, read_bars, save_bars, symbol, validate_frame
from sentinel.sigma import PAIRS


def append_days(directory, start='2026-10-01', end='2026-10-09'):
    root=Path(directory)
    cache=root/'archives'
    cache.mkdir(parents=True,exist_ok=True)
    tasks=[(pair,str(day.date())) for pair in PAIRS for day in pd.date_range(start,end)]
    def fetch(task):
        pair,day=task
        name=f'{symbol(pair)}-1h-{day}.zip'
        url=f'https://data.binance.vision/data/spot/daily/klines/{symbol(pair)}/1h/{name}'
        p=cache/name
        b=p.read_bytes() if p.exists() else get_bytes(url)
        check=cache/(name+'.CHECKSUM')
        c=check.read_bytes() if check.exists() else get_bytes(url+'.CHECKSUM')
        actual=hashlib.sha256(b).hexdigest()
        if actual!=c.decode().split()[0]:raise ValueError(f'Checksum failed: {name}')
        p.write_bytes(b)
        check.write_bytes(c)
        with zipfile.ZipFile(io.BytesIO(b)) as z:
            raw=pd.read_csv(io.BytesIO(z.read(name[:-4]+'.csv')),header=None,names=RAW_COLUMNS)
        frame=normalize_frame(raw)
        if len(frame)!=24:raise ValueError(f'Incomplete UTC day: {name}')
        return pair,frame,{'pair':pair,'day':day,'url':url,'sha256':actual,'rows':len(frame)}
    rows=[]
    with ThreadPoolExecutor(max_workers=8) as pool:
        result=list(pool.map(fetch,tasks))
    for pair in PAIRS:
        d=normalize_frame(pd.concat([read_bars(root,pair)]+[frame for p,frame,_ in result if p==pair],ignore_index=True))
        validate_frame(d)
        gaps=d.timestamp.diff().dropna()
        print(json.dumps({'pair':pair,'missing_hours':int(((gaps//3600000)-1).sum()),'gap_policy':'no invented bars; reset signal warmup after gaps'}),flush=True)
        save_bars(root,pair,d)
        print(json.dumps({'pair':pair,'rows':len(d),'last_open_utc':pd.to_datetime(int(d.timestamp.iloc[-1]),unit='ms',utc=True).isoformat()}),flush=True)
    meta={'start':start,'end_inclusive':end,'archives':[m for _,_,m in result]}
    (root/'daily_manifest.json').write_text(json.dumps(meta,indent=2))
    return meta


if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--directory',default='data/sigma')
    ap.add_argument('--daily-only',action='store_true')
    a=ap.parse_args()
    if not a.daily_only:download_history(list(PAIRS),'2022-01-01','2026-09-30',a.directory,workers=8)
    append_days(a.directory)
