import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools' / 'frame_traffic'))
import copy,re
import pytest
from plan import CONFIG,network_plan,corefile,validate

def test_disabled_by_default():
 assert CONFIG['enabled'] is False
 assert CONFIG['router_policy_mirrored'] is False
 assert CONFIG['tv_ipv6_disabled'] is False

def test_scope_and_preserve_docker():
 p=network_plan(CONFIG)
 assert len(p['owned_chains'])==3 and len(p['jumps'])==3
 for rule in p['rules']:
  assert CONFIG['tv_ip']+'/32' in rule
  assert 'DOCKER' not in str(rule)
 assert sum('--mac-source' in rule for rule in p['rules'])==3
 assert any('ESTABLISHED,RELATED' in rule for rule in p['rules'])

def test_bad_address_or_shell_input_rejected():
 for key,value in [('interface','wlan0; reboot'),('tv_ip','1.1.1.1'),('tv_mac','any'),('dns_port',53),('blocked_domains',['x;reboot'])]:
  c=copy.deepcopy(CONFIG);c[key]=value
  with pytest.raises((AssertionError,ValueError)):validate(c)

def test_dns_preserves_blocks_and_restricts_client():
 s=corefile(CONFIG)
 assert 'allow net '+CONFIG['tv_ip']+'/32' in s
 assert 'NXDOMAIN' in s and 'fallthrough' in s
 for name in CONFIG['blocked_domains']:assert re.escape(name) in s
