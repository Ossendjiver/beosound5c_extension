#!/usr/bin/env python3
"""Install optional LAN-only, read-only music sharing without restarting players."""
import argparse
import datetime
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def lan_settings(routes, addresses):
    route = next((row for row in routes if row.get("dst") == "default" and row.get("dev")), None)
    if not route:
        raise ValueError("No default LAN route")
    interface = route["dev"]
    if not re.fullmatch(r"[a-zA-Z0-9_.:-]+", interface):
        raise ValueError("Invalid LAN interface")
    device = next((row for row in addresses if row.get("ifname") == interface), {})
    addr = next((a for a in device.get("addr_info", []) if a.get("family") == "inet" and a.get("scope") == "global"), None)
    if not addr or not ipaddress.ip_address(addr["local"]).is_private:
        raise ValueError("A private IPv4 LAN address is required")
    network = str(ipaddress.ip_interface(f"{addr['local']}/{addr['prefixlen']}").network)
    return interface, network


def rendered_config(template, interface):
    if not re.fullmatch(r"[a-zA-Z0-9_.:-]+", interface):
        raise ValueError("Invalid LAN interface")
    return template.replace("@LAN_INTERFACE@", interface)


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="."+path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(data); handle.flush(); os.fsync(handle.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-package", action="store_true", help="Install Debian minidlna without upgrading existing packages")
    args=parser.parse_args()
    if os.geteuid()!=0: raise SystemExit("Run as root")
    if not os.path.ismount('/media/local'): raise SystemExit("SSD is not mounted; refusing SD-card fallback")
    music=Path('/media/local/music')
    if not music.is_dir(): raise SystemExit("Music folder is missing")
    root=Path(__file__).resolve().parents[1]
    unit=(root/'services/system/beo-music-share.service').read_text()
    template=(root/'config/music-share.conf.example').read_text()
    cache_unit=(root/'services/system/beo-music-share-cache.service').read_text()
    interface,network=lan_settings(json.loads(subprocess.check_output(['ip','-j','route','show','default'])),json.loads(subprocess.check_output(['ip','-j','address','show'])))
    config=rendered_config(template,interface)
    firewall='[Service]\nIPAddressDeny=any\nIPAddressAllow=localhost\nIPAddressAllow='+network+'\nIPAddressAllow=239.255.255.250/32\n'
    paths={Path('/etc/systemd/system/beo-music-share-cache.service'):cache_unit,Path('/etc/beosound5c/music-share.conf'):config,Path('/etc/systemd/system/beo-music-share.service'):unit,Path('/etc/systemd/system/beo-music-share.service.d/lan-only.conf'):firewall}
    backup_root=Path('/media/local/cache/music-share-config-backups')
    backup_root.mkdir(parents=True,exist_ok=True)
    changed={p:data for p,data in paths.items() if not p.exists() or p.read_text()!=data}
    old={str(p):p.read_text() if p.exists() else None for p in changed}
    backup=backup_root/(datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    if changed: backup.write_text(json.dumps(old,indent=2))
    installed=Path('/usr/sbin/minidlnad').exists()
    if not installed:
        if not args.install_package: raise SystemExit('Install minidlna first or use --install-package')
        # Suppress package auto-start until the SSD-only custom service is configured.
        mask=Path('/run/systemd/system/minidlna.service')
        if mask.exists() or mask.is_symlink(): raise SystemExit('minidlna already has a runtime unit override; inspect manually')
        subprocess.run(['systemctl','mask','--runtime','minidlna.service'],check=True)
        try:
            env=dict(os.environ,DEBIAN_FRONTEND='noninteractive',NEEDRESTART_MODE='l')
            subprocess.run(['apt-get','--no-install-recommends','--no-upgrade','-y','install','minidlna'],check=True,env=env)
            subprocess.run(['systemctl','disable','minidlna.service'],check=True)
        finally:
            subprocess.run(['systemctl','unmask','--runtime','minidlna.service'],check=True)
    elif subprocess.run(['systemctl','is-active','--quiet','minidlna.service']).returncode==0:
        raise SystemExit('An existing minidlna server is active; refusing to replace it')
    image=Path('/media/local/cache/music-share-cache.ext4')
    cache=Path('/run/beo-music-share-cache')
    if not image.exists():
        if not shutil.which('mkfs.ext4'): raise SystemExit('e2fsprogs is required')
        if shutil.disk_usage(image.parent).free < 2*1024**3: raise SystemExit('Need 2 GiB free on SSD')
        fd=os.open(image,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as handle:
            handle.truncate(1024**3); handle.flush(); os.fsync(handle.fileno())
        subprocess.run(['mkfs.ext4','-q','-F','-m','0',str(image)],check=True)
    if subprocess.check_output(['blkid','-s','TYPE','-o','value',str(image)],text=True).strip()!='ext4':
        raise SystemExit('Existing cache image is not ext4; refusing to format it')
    # Stop only this optional server to migrate its index path, never players or MA.
    if changed:
        subprocess.run(['systemctl','stop','beo-music-share.service'],check=False)
    for path,data in changed.items(): write_atomic(path,data)
    subprocess.run(['systemd-analyze','verify','/etc/systemd/system/beo-music-share.service'],check=True)
    subprocess.run(['systemctl','daemon-reload'],check=True)
    subprocess.run(['systemctl','start','beo-music-share-cache.service'],check=True)
    subprocess.run(['chown','minidlna:minidlna',str(cache)],check=True)
    subprocess.run(['chmod','0750',str(cache)],check=True)
    subprocess.run(['runuser','-u','minidlna','--','test','-r',str(music)],check=True)
    subprocess.run(['runuser','-u','minidlna','--','test','-w',str(cache)],check=True)
    subprocess.run(['systemctl','enable','beo-music-share.service'],check=True)
    subprocess.run(['systemctl','restart' if changed else 'start','beo-music-share.service'],check=True)
    subprocess.run(['systemctl','is-active','--quiet','beo-music-share.service'],check=True)
    # Retain just two snapshots belonging to this installer, after a successful start.
    for previous in sorted(backup_root.glob('*.json'), key=lambda p:p.name, reverse=True)[2:]: previous.unlink()
    print(json.dumps({'state':'active','name':'BS5c Music','interface':interface,'allowed_network':network,'music_root':str(music),'index_root':str(cache),'ssd_cache_image':str(image),'configuration_changed':bool(changed)}))

if __name__=='__main__': main()
