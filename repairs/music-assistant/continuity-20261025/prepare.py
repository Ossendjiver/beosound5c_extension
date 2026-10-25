"""Prepare pinned MA repairs outside the installed package; never deploy or restart."""
from pathlib import Path
import argparse,hashlib,json,shutil,subprocess,tempfile
ROOT=Path(__file__).resolve().parent

def prepare(source, output):
    source=source.resolve();output=output.resolve()
    if source==output or source in output.parents or output in source.parents:
        raise ValueError('Use a separate output directory outside the source tree')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Output must be absent or empty')
    pinned=json.loads((ROOT/'preimages.json').read_text())
    for rel,sha in pinned.items():
        if hashlib.sha256((source/rel).read_bytes()).hexdigest()!=sha:
            raise ValueError('Source changed; rebase and retest instead of forcing: '+rel)
    for name in ['tidal_dash_routes.py','stream_failure_recovery.py']:
        if (source/'helpers'/name).exists():
            raise ValueError('Repair helper already installed; inspect before replacing')
    # Failed patch or schema checks leave both the source and final output untouched.
    with tempfile.TemporaryDirectory(prefix='ma-continuity-prepare-') as temp:
        work=Path(temp)
        for rel in pinned:
            p=work/rel;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source/rel,p)
        patch=(ROOT/'music-assistant.patch').read_bytes()
        for mode in [['--dry-run'],[]]:
            r=subprocess.run(['patch','--batch','--forward','-p1',*mode],cwd=work,input=patch,capture_output=True)
            if r.returncode:raise ValueError('Pinned patch did not apply cleanly')
        for p in (ROOT/'helpers').glob('*.py'):
            target=work/'helpers'/p.name;target.parent.mkdir(exist_ok=True);shutil.copy2(p,target)
        for p in work.rglob('*.py'):compile(p.read_text(),str(p),'exec')
        output.mkdir(parents=True,exist_ok=True)
        for p in work.rglob('*.py'):
            target=output/p.relative_to(work);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
    return sorted(str(p.relative_to(output)) for p in output.rglob('*.py'))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    print(json.dumps({'prepared':prepare(a.source,a.output),'deployed':False}))
