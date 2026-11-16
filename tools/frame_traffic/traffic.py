"""TV-only flow/DNS metadata recorder. No payloads or URL decryption.

Two independent units: --mode flow uses CAP_NET_ADMIN for conntrack events;
--mode dns launches an unprivileged CoreDNS binary with bounded stdout capture.
All data stays on SSD. Gateway rules are a separate explicit activation step.
"""
import argparse,datetime as dt,json,os,re,socket,struct,subprocess,threading,time,urllib.request
from pathlib import Path


def attrs(data):
    result={};pos=0
    while pos+4<=len(data):
        length,kind=struct.unpack_from('=HH',data,pos)
        if length<4 or pos+length>len(data):raise ValueError('Bad netlink attribute')
        result[kind & 0x3fff]=data[pos+4:pos+length]
        pos+=(length+3)&~3
    return result


def parse_flows(data,tv):
    rows=[];pos=0
    while pos+16<=len(data):
        length,kind,flags,seq,pid=struct.unpack_from('=IHHII',data,pos)
        if length<16 or pos+length>len(data):raise ValueError('Bad netlink message')
        packet=data[pos+16:pos+length];pos+=(length+3)&~3
        if kind==2:raise ValueError('Netlink error notification')
        if kind not in (0x100,0x102) or len(packet)<4 or packet[0]!=socket.AF_INET:continue
        top=attrs(packet[4:]);orig=attrs(top.get(1,b''));ips=attrs(orig.get(1,b''));proto=attrs(orig.get(2,b''))
        if len(ips.get(1,b''))!=4 or socket.inet_ntop(socket.AF_INET,ips[1])!=tv:continue
        if len(ips.get(2,b''))!=4:continue
        protocol=proto.get(1,b'\x00')[0]
        def port(k):return struct.unpack('!H',proto[k])[0] if len(proto.get(k,b''))==2 else None
        row={'event':'flow_end' if kind==0x102 else 'flow_open_or_update','remote_ip':socket.inet_ntop(socket.AF_INET,ips[2]),'protocol':protocol,'source_port':port(2),'remote_port':port(3)}
        if len(top.get(12,b''))==4:row['connection_id']=struct.unpack('!I',top[12])[0]
        for key,label in [(9,'outbound_bytes'),(10,'inbound_bytes')]:
            count=attrs(top.get(key,b''));value=count.get(2)
            if value and len(value)==8:row[label]=struct.unpack('!Q',value)[0]
        rows.append(row)
    return rows


class Audit:
    def __init__(self,path,max_bytes=50*1024*1024,part_bytes=5*1024*1024,days=7):
        self.path=Path(path);self.path.mkdir(parents=True,exist_ok=True)
        self.max_bytes=max_bytes;self.part_bytes=part_bytes;self.days=days;self.lock=threading.Lock();self.file=None;self.day=''
    def emit(self,event,**data):
        now=time.time();day=dt.datetime.fromtimestamp(now,dt.timezone.utc).strftime('%Y%m%d')
        raw=(json.dumps({'ts':now,'event':event,**data},ensure_ascii=True)+'\n').encode()
        if len(raw)>8192:return
        with self.lock:
            try:
                if self.file is None or self.day!=day or self.file.stat().st_size+len(raw)>self.part_bytes:
                    self.day=day;self.file=self.path/f'{day}-{time.time_ns()}.jsonl';self.file.touch()
                files=sorted(self.path.glob('*.jsonl'),key=lambda p:p.name)
                total=sum(p.stat().st_size for p in files)
                for p in files:
                    if p==self.file:continue
                    if p.stat().st_mtime<now-self.days*86400 or total+len(raw)>self.max_bytes:
                        size=p.stat().st_size;p.unlink();total-=size
                if total+len(raw)>self.max_bytes:return
                with self.file.open('ab') as out:out.write(raw)
            except OSError:
                # Logging/storage failure must never change firewall or playback.
                return


def watch_incidents(audit,stop):
    seen_absent=False;last=None;unavailable=False
    while not stop.is_set():
        try:
            with urllib.request.urlopen('http://127.0.0.1:8788/library/youtube-watchdog',timeout=3) as r:state=json.load(r)
            unavailable=False
            if state.get('last_result')=='absent' and not state.get('present'):seen_absent=True
            incident=state.get('incident')
            if state.get('last_result')=='present' and incident and incident!=last:
                audit.emit('youtube_return_detected' if seen_absent else 'youtube_preexisting_at_capture',incident=incident)
                last=incident
        except Exception:
            if not unavailable:audit.emit('watchdog_status_unavailable');unavailable=True
        stop.wait(15)


def flows(config,audit):
    # Linux nf_conntrack NEW and DESTROY multicast groups. Kernel supplies
    # original tuples even after NAT; no traffic belonging to other IPs is saved.
    sock=socket.socket(socket.AF_NETLINK,socket.SOCK_RAW,12)
    sock.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,1024*1024)
    sock.bind((0,5));sock.settimeout(5)
    stop=threading.Event();threading.Thread(target=watch_incidents,args=(audit,stop),daemon=True).start()
    audit.emit('capture_started',tv_ip=config['tv_ip'],coverage='IPv4 conntrack plus DNS; no packet payloads')
    try:
        while True:
            try:
                for row in parse_flows(sock.recv(1024*1024),config['tv_ip']):
                    event=row.pop('event');audit.emit(event,**row)
            except socket.timeout:continue
            except (OSError,ValueError) as exc:audit.emit('capture_gap_or_parse_error',error=type(exc).__name__)
    finally:stop.set();sock.close()


def dns(audit,binary,corefile):
    p=subprocess.Popen([binary,'-conf',corefile],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
    try:
        for line in p.stdout:
            # Keep query names/response codes only, never dump arbitrary output.
            m=re.search(r'frame-dns (\S+) (\S+) (\S+) (\S+) (\S+)',line)
            if m:audit.emit('dns',client=m[1],query_type=m[2],name=m[3],rcode=m[4],duration=m[5])
        raise RuntimeError('DNS process exited: '+str(p.wait()))
    finally:
        if p.poll() is None:p.terminate();p.wait(timeout=5)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--mode',choices=['flow','dns'],required=True);p.add_argument('--config',required=True);p.add_argument('--data',default='/media/local/cache/frame-traffic');p.add_argument('--coredns',default='/media/local/cache/frame-traffic/bin/coredns');p.add_argument('--corefile',default='/etc/beosound5c/frame-traffic.Corefile');a=p.parse_args()
    c=json.loads(Path(a.config).read_text())
    mount=subprocess.run(['findmnt','-T',a.data,'-n','-o','TARGET,SOURCE'],capture_output=True,text=True,check=True).stdout.split()
    if not (Path(a.data).resolve().is_relative_to('/media/local') and len(mount)>1 and mount[1].startswith('/dev/sd')):raise SystemExit('SSD mount required')
    audit=Audit(Path(a.data)/a.mode)
    if a.mode=='flow':flows(c,audit)
    else:dns(audit,a.coredns,a.corefile)
