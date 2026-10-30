"""Read-only generator: never runs sysctl, iptables, or network commands."""
import ipaddress,json,re
from pathlib import Path

CONFIG={'interface':'wlan0','tv_ip':'192.168.1.50','tv_mac':'02:00:00:00:00:50','bs5c_ip':'192.168.1.51','router_ip':'192.168.1.1','lan':'192.168.1.0/24','dns_port':1053,'enabled':False,'tv_ipv6_disabled':False,'router_policy_mirrored':False,'ipv4_dns_only':True,'allow_ipv4_only_capture':False,
'blocked_domains':['osb-apps.samsungqbe.com','osb-apps-v2.samsungqbe.com','osb-v2.samsungqbe.com','osb.samsungqbe.com','osb-eusvc.samsungqbe.com','osb-krsvc.samsungqbe.com']}

def validate(c):
 net=ipaddress.IPv4Network(c['lan']);assert net.is_private
 ips=[ipaddress.IPv4Address(c[k]) for k in ('tv_ip','bs5c_ip','router_ip')]
 assert len(set(ips))==3 and all(ip in net for ip in ips)
 assert re.fullmatch(r'[a-zA-Z0-9_.-]{1,15}',c['interface'])
 assert re.fullmatch(r'(?:[a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}',c['tv_mac'])
 assert 1024<=int(c['dns_port'])<=65535
 for name in c['blocked_domains']:assert re.fullmatch(r'[a-z0-9.-]+',name) and '.' in name
 return c

def network_plan(c):
 validate(c);i=c['interface'];tv=c['tv_ip']+'/32';guard=['-i',i,'-s',tv,'-m','mac','--mac-source',c['tv_mac']]
 chains=[['filter','BS5C_FRAME_DIAG_F'],['nat','BS5C_FRAME_DIAG_D'],['nat','BS5C_FRAME_DIAG_N']]
 rules=[['filter','BS5C_FRAME_DIAG_F',*guard,'-o',i,'-j','ACCEPT'],['filter','BS5C_FRAME_DIAG_F','-i',i,'-o',i,'-d',tv,'-m','conntrack','--ctstate','ESTABLISHED,RELATED','-j','ACCEPT'],['nat','BS5C_FRAME_DIAG_N','-s',tv,'-o',i,'!','-d',c['lan'],'-j','MASQUERADE']]
 for protocol in ('tcp','udp'):rules.append(['nat','BS5C_FRAME_DIAG_D',*guard,'-p',protocol,'--dport','53','-j','DNAT','--to-destination',f"{c['bs5c_ip']}:{c['dns_port']}"])
 jumps=[['filter','FORWARD','-j','BS5C_FRAME_DIAG_F'],['nat','PREROUTING','-j','BS5C_FRAME_DIAG_D'],['nat','POSTROUTING','-j','BS5C_FRAME_DIAG_N']]
 return {'owned_chains':chains,'rules':rules,'jumps':jumps,'sysctls':{'net.netfilter.nf_conntrack_acct':'1','net.ipv4.ip_forward':'1','net.ipv4.conf.all.send_redirects':'0',f'net.ipv4.conf.{i}.send_redirects':'0'}}

def corefile(c):
 validate(c);names='|'.join(re.escape(n)+r'\.' for n in c['blocked_domains'])
 ipv4_only = ' template IN AAAA {\n  rcode NOERROR\n }\n' if c.get('ipv4_dns_only') else ''
 return f'''.:{c['dns_port']} {{
 bind {c['bs5c_ip']}
 acl {{
  allow net {c['tv_ip']}/32
  block
 }}
 log . "frame-dns {{remote}} {{type}} {{name}} {{rcode}} {{duration}}"
 errors
 template IN ANY {{
  match "(?i)(^|\\.)({names})$"
  rcode NXDOMAIN
  fallthrough
 }}
{ipv4_only} forward . {c['router_ip']} {{
  max_concurrent 32
 }}
 cache 30
}}
'''

if __name__=='__main__':
 p=Path(__file__).parent
 (p/'private-config.draft.json').write_text(json.dumps(CONFIG,indent=2)+'\n')
 (p/'network-plan.json').write_text(json.dumps(network_plan(CONFIG),indent=2)+'\n')
 (p/'Corefile.draft').write_text(corefile(CONFIG))
 print('Generated disabled plan and DNS draft; no network commands executed')
