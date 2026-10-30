"""Explicit, guarded activation/rollback. Run only after DNS/collector readiness.

No work is performed on import. --plan is the default; no broad firewall flush.
"""
import argparse,json,socket,subprocess
from pathlib import Path
from plan import network_plan,validate

STATE=Path('/media/local/cache/frame-traffic/owned-network-state.json')

def run(args,check=True):
 return subprocess.run(args,capture_output=True,text=True,check=check,timeout=10)

def ipt(table,operation,chain,*args,check=True):
 return run(['iptables','-w','5','-t',table,operation,chain,*args],check=check)

def activate(c):
 validate(c)
 if not (c.get('enabled') is True and c.get('router_policy_mirrored') is True
         and (c.get('tv_ipv6_disabled') is True or
              (c.get('ipv4_dns_only') is True and c.get('allow_ipv4_only_capture') is True))):
  raise RuntimeError('Disabled: mirrored policy and explicit IPv6-disabled or IPv4-only coverage required')
 if STATE.exists():raise RuntimeError('Owned setup exists; inspect/rollback instead of overwriting')
 mount=run(['findmnt','-T',str(STATE.parent),'-n','-o','TARGET,SOURCE']).stdout.split()
 if mount[0]!='/media/local' or not mount[1].startswith('/dev/sd'):raise RuntimeError('SSD required')
 run(['systemctl','is-active','--quiet','beo-frame-traffic-flow.service','beo-frame-traffic-dns.service'])
 with socket.create_connection((c['bs5c_ip'],c['dns_port']),timeout=3):pass
 p=network_plan(c)
 for table,chain in p['owned_chains']:
  if ipt(table,'-S',chain,check=False).returncode==0:raise RuntimeError('Existing chain, will not overwrite: '+chain)
 state={'config':c,'chains':p['owned_chains'],'jumps':p['jumps'],'previous_sysctls':{k:run(['sysctl','-n',k]).stdout.strip() for k in p['sysctls']}}
 STATE.parent.mkdir(parents=True,exist_ok=True)
 def save():
  tmp=STATE.with_suffix('.tmp');tmp.write_text(json.dumps(state,indent=2)+'\n');tmp.replace(STATE)
 save()
 try:
  for table,chain in p['owned_chains']:
   ipt(table,'-N',chain)
  for table,chain,*args in p['rules']:ipt(table,'-A',chain,*args)
  for k,v in p['sysctls'].items():run(['sysctl','-w',k+'='+v])
  # Link last. Preparing the owned chains has no effect on forwarding.
  for table,chain,*args in p['jumps']:
   ipt(table,'-I',chain,'1',*args)
 except BaseException:
  rollback()
  raise

def rollback():
 state=json.loads(STATE.read_text());plan=network_plan(state['config'])
 for table,chain,*args in reversed(state['jumps']):
  assert [table,chain,*args] in plan['jumps'];ipt(table,'-D',chain,*args,check=False)
 for table,chain in reversed(state['chains']):
  assert [table,chain] in plan['owned_chains']
  if ipt(table,'-S',chain,check=False).returncode==0:
   ipt(table,'-F',chain);ipt(table,'-X',chain)
 for k,old in state['previous_sysctls'].items():
  assert k in plan['sysctls']
  if run(['sysctl','-n',k]).stdout.strip()==plan['sysctls'][k]:run(['sysctl','-w',k+'='+old])
 STATE.unlink()

def ensure(c):
 """Idempotent boot recovery, without trusting stale persistent firewall state."""
 validate(c)
 if STATE.exists():
  old=json.loads(STATE.read_text())
  if old['config']!=c:raise RuntimeError('Configuration changed; explicit rollback required')
  # If all exact owned rules are still present, leave them alone.
  p=network_plan(c)
  complete=True
  for table,chain,*args in p['rules']+p['jumps']:
   if ipt(table,'-C',chain,*args,check=False).returncode:complete=False
  if complete:
   for k,v in p['sysctls'].items():run(['sysctl','-w',k+'='+v])
   return
  # Reboot loses runtime rules but not SSD state; remove only recorded owned
  # remnants, restore their settings, and reconstruct the guarded setup.
  rollback()
 # DNS process startup can lag its systemd active transition briefly.
 import time
 for _ in range(30):
  try:
   with socket.create_connection((c['bs5c_ip'],c['dns_port']),timeout=1):break
  except OSError:time.sleep(.2)
 activate(c)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('config');p.add_argument('--action',choices=['plan','apply','ensure','rollback'],default='plan');a=p.parse_args()
 c=validate(json.loads(Path(a.config).read_text()))
 if a.action=='plan':print(json.dumps(network_plan(c),indent=2))
 elif a.action=='apply':activate(c)
 elif a.action=='ensure':ensure(c)
 else:rollback()
