import importlib.util
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[3]
p=Path(ROOT)/'tools/install_music_share.py'
spec=importlib.util.spec_from_file_location('music_share_installer',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def test_private_lan_is_derived_not_hardcoded():
 routes=[{'dst':'default','dev':'wlan0'}]
 addrs=[{'ifname':'wlan0','addr_info':[{'family':'inet','scope':'global','local':'192.168.4.103','prefixlen':24}]}]
 assert m.lan_settings(routes,addrs)==('wlan0','192.168.4.0/24')
 addrs[0]['addr_info'][0]['local']='10.1.2.3'
 assert m.lan_settings(routes,addrs)==('wlan0','10.1.2.0/24')

def test_no_route_and_public_addresses_are_rejected():
 with pytest.raises(ValueError):m.lan_settings([],[])
 routes=[{'dst':'default','dev':'wlan0'}]
 addrs=[{'ifname':'wlan0','addr_info':[{'family':'inet','scope':'global','local':'8.8.8.8','prefixlen':24}]}]
 with pytest.raises(ValueError):m.lan_settings(routes,addrs)
 with pytest.raises(ValueError):m.rendered_config('x','wlan0\nmedia_dir=/etc')

def test_only_music_is_shared_and_index_stays_on_ssd():
 r=Path(ROOT);conf=(r/'config/music-share.conf.example').read_text()
 out=m.rendered_config(conf,'wlan0')
 assert 'network_interface=wlan0' in out
 assert [x for x in out.splitlines() if x.startswith('media_dir=')]==['media_dir=A,/media/local/music']
 assert 'db_dir=/run/beo-music-share-cache' in out
 assert 'wide_links=no' in out and 'inotify=yes' in out
 unit=(r/'services/system/beo-music-share.service').read_text()
 assert 'ConditionPathIsMountPoint=/media/local' in unit
 assert 'ReadOnlyPaths=/media/local/music' in unit and 'CPUQuota=20%' in unit
 assert ' -R ' not in unit

def test_atomic_replacement(tmp_path):
 path=tmp_path/'conf';path.write_text('old')
 m.write_atomic(path,'new')
 assert path.read_text()=='new' and [x.name for x in tmp_path.iterdir()]==['conf']
