"""Create the complete, compact source/research delivery without private state."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def included(path,root):
    rel=path.relative_to(root)
    if any(p in {'.git','.venv','__pycache__','runtime','tmp','archives','daily_archives'} for p in rel.parts):
        return False
    if path.name in {'.env','PACKAGE_MANIFEST.json'} or path.suffix in {'.pyc','.lock','.sqlite3','.zip'}:
        return False
    if rel.parts[:2]==('data','sigma') and path.suffix=='.csv':
        return False
    if rel.parts[:2]==('data','limit_minutes') and path.suffix not in {'.json','.md','.txt'}:
        return False
    # Keep every candidate's NAV, fills and summary. Repeated hourly debug
    # diagnostics remain reproducible, while the selected/baseline series are
    # bundled for immediate inspection.
    if path.name=='decisions.csv' and 'comparisons' in rel.parts:
        candidate=rel.parts[rel.parts.index('comparisons')+1]
        if candidate not in {'combined-conservative','sigma-reference-corrected'}:
            return False
    return path.is_file()


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--output',required=True)
    ap.add_argument('--source-commit',required=True)
    a=ap.parse_args()
    root=Path(__file__).resolve().parents[1]
    out=Path(a.output).resolve();out.parent.mkdir(parents=True,exist_ok=True)
    files=sorted(p for p in root.rglob('*') if p.resolve()!=out and included(p,root))
    hashes={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    manifest={'source_commit':a.source_commit,'upstream_commit':'64e0856d99791aab00c175687d1b354ae1490c8a','config_hash':'26ee5f485f47231d7957797c80a86db6ff8b51981dd56449f18541cf8cd4fe84','tests':json.loads((root/'research/sigma_gate/regression_result.json').read_text()),'data':json.loads((root/'data/sigma/processed_manifest.json').read_text()),'simulation_and_actual_execution_are_distinct':True,'profitability_proven':False,'files_sha256':hashes,'excludes':['Git internals','runtime journals','credentials','raw download caches','legacy minute datasets','reproducible hourly debug diagnostics for other comparison candidates']}
    with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in files:z.write(p,'roostoo_sentinel/'+str(p.relative_to(root)))
        z.writestr('roostoo_sentinel/PACKAGE_MANIFEST.json',json.dumps(manifest,indent=2)+'\n')
    with zipfile.ZipFile(out) as z:
        assert z.testzip() is None
        for name,digest in hashes.items():
            assert hashlib.sha256(z.read('roostoo_sentinel/'+name)).hexdigest()==digest,name
    print(json.dumps({'archive':str(out),'bytes':out.stat().st_size,'files':len(files)+1,'sha256':hashlib.sha256(out.read_bytes()).hexdigest(),'crc_and_manifest_verified':True}),flush=True)


if __name__=='__main__':main()
