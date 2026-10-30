import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools' / 'frame_traffic'))
import copy,json
import pytest
import activate
from plan import CONFIG

def test_guards_stop_before_any_command(monkeypatch):
 monkeypatch.setattr(activate,'run',lambda *a,**k:pytest.fail('Unexpected network command'))
 with pytest.raises(RuntimeError,match='Disabled'):activate.activate(CONFIG)

def test_owned_existing_setup_is_not_overwritten(tmp_path,monkeypatch):
 p=tmp_path/'state';p.write_text('{}');monkeypatch.setattr(activate,'STATE',p)
 c=copy.deepcopy(CONFIG)
 for k in ('enabled','tv_ipv6_disabled','router_policy_mirrored'):c[k]=True
 monkeypatch.setattr(activate,'run',lambda *a,**k:pytest.fail('Unexpected network command'))
 with pytest.raises(RuntimeError,match='exists'):activate.activate(c)

def test_rollback_only_owns_scoped_rules_and_restores_unchanged_sysctls(tmp_path,monkeypatch):
 from plan import network_plan
 c=copy.deepcopy(CONFIG);p=network_plan(c);state=tmp_path/'state'
 state.write_text(json.dumps({'config':c,'chains':p['owned_chains'],'jumps':p['jumps'],'previous_sysctls':{k:'1' for k in p['sysctls']}}))
 monkeypatch.setattr(activate,'STATE',state);calls=[]
 class R:stdout='0';returncode=0
 def run(args,**kwargs):calls.append(args);return R()
 monkeypatch.setattr(activate,'run',run);activate.rollback()
 assert not state.exists()
 for args in calls:
  if args[0]=='iptables':
   assert 'DOCKER' not in str(args) and '-P' not in args
   if '-F' in args or '-X' in args:assert args[-1].startswith('BS5C_FRAME_DIAG_')

def test_apply_records_complete_owned_plan_before_creating_any_chain(tmp_path,monkeypatch):
 import socket
 c=copy.deepcopy(CONFIG)
 for k in ('enabled','tv_ipv6_disabled','router_policy_mirrored'):c[k]=True
 state=tmp_path/'state';monkeypatch.setattr(activate,'STATE',state)
 class Socket:
  def __enter__(self):return self
  def __exit__(self,*a):pass
 monkeypatch.setattr(socket,'create_connection',lambda *a,**k:Socket())
 class R:stdout='';returncode=0
 def run(args,check=True):
  r=R()
  if args[0]=='findmnt':r.stdout='/media/local /dev/sda1'
  elif args[0]=='sysctl' and '-n' in args:r.stdout='1'
  elif args[0]=='iptables' and '-S' in args:r.returncode=1
  elif args[0]=='iptables' and '-N' in args:
   saved=json.loads(state.read_text())
   from plan import network_plan
   assert saved['chains']==network_plan(c)['owned_chains']
   assert saved['jumps']==network_plan(c)['jumps']
  return r
 monkeypatch.setattr(activate,'run',run);activate.activate(c)
 assert state.exists()
