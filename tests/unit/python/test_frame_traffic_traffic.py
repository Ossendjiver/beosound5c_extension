import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools' / 'frame_traffic'))
import json,socket,struct
from pathlib import Path
import pytest
from traffic import attrs,parse_flows,Audit

def attr(k,v):
 b=struct.pack('=HH',len(v)+4,k)+v
 return b+b'\0'*((-len(b))%4)

def packet(ip,kind=0x100):
 ips=attr(1,socket.inet_aton(ip))+attr(2,socket.inet_aton('203.0.113.7'))
 proto=attr(1,b'\x11')+attr(2,struct.pack('!H',12345))+attr(3,struct.pack('!H',443))
 body=b'\x02\0\0\0'+attr(1,attr(1,ips)+attr(2,proto))+attr(9,attr(2,struct.pack('!Q',123)))
 return struct.pack('=IHHII',len(body)+16,kind,0,0,0)+body

def test_nat_original_tuple_udp_and_byte_counts():
 rows=parse_flows(packet('192.168.4.109'), '192.168.4.109')
 assert rows[0]['remote_ip']=='203.0.113.7' and rows[0]['protocol']==17
 assert rows[0]['remote_port']==443 and rows[0]['outbound_bytes']==123
 assert 'inbound_bytes' not in rows[0]
 assert parse_flows(packet('192.168.4.109',0x102),'192.168.4.109')[0]['event']=='flow_end'

def test_other_devices_never_logged():
 assert parse_flows(packet('192.168.4.110'),'192.168.4.109')==[]

def test_malformed_events_rejected():
 with pytest.raises(ValueError):attrs(struct.pack('=HH',3,1))
 with pytest.raises(ValueError):parse_flows(struct.pack('=IHHII',3000,0x100,0,0,0),'192.168.4.109')

def test_logs_bounded_and_payload_free(tmp_path):
 a=Audit(tmp_path,max_bytes=2000,part_bytes=300)
 for n in range(100):a.emit('flow_open_or_update',remote_ip='203.0.113.7',remote_port=n)
 files=list(tmp_path.glob('*.jsonl'))
 assert sum(p.stat().st_size for p in files)<=2000
 for p in files:
  for line in p.read_text().splitlines():assert json.loads(line)['event']=='flow_open_or_update'
