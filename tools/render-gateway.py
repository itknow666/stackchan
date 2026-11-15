"""Generate private Caddy and device access files without changing any service."""
import argparse,json,os,re,secrets
from pathlib import Path

parser=argparse.ArgumentParser()
parser.add_argument('config',type=Path)
parser.add_argument('--output',type=Path,default=Path('private/gateway'))
args=parser.parse_args()
cfg=json.loads(args.config.read_text(encoding='utf-8'))
domain=cfg['domain'];device=cfg['device_id'];upstream=cfg.get('upstream','127.0.0.1:12800')
if not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?',domain):raise SystemExit('Invalid domain')
if not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}',device):raise SystemExit('Invalid device identifier')
if not re.fullmatch(r'127\.0\.0\.1:[0-9]{1,5}',upstream):raise SystemExit('Upstream must be a loopback listener')
os.umask(0o077)
args.output.mkdir(parents=True,exist_ok=True)
access_path=args.output/'device-access.json'
if access_path.exists():
    access=json.loads(access_path.read_text())
    if access['domain']!=domain:raise SystemExit('Existing domain differs; preserve or move the old private configuration first')
    prefix=access['prefix']
    if not re.fullmatch(r'[0-9a-f]{48}',prefix):raise SystemExit('Invalid saved prefix')
else:
    prefix=secrets.token_hex(24)
    access={'domain':domain,'prefix':prefix,'ota_url':f'https://{domain}/{prefix}/xiaozhi/ota/','ws_url':f'wss://{domain}/{prefix}/xiaozhi/ws'}
    access_path.write_text(json.dumps(access,indent=2)+'\n')
ota=json.dumps({'websocket':{'url':access['ws_url'],'token':'','version':3}},separators=(',',':'))
config='''{
    admin 127.0.0.1:12019
    log {
        level ERROR
    }
}
DOMAIN {
    @ota path /PREFIX/xiaozhi/ota/
    handle @ota {
        header Content-Type application/json
        respond `OTA_JSON` 200
    }
    @robot {
        path /PREFIX/xiaozhi/ws
        header_regexp Device-Id (?i)^DEVICE_ID$
    }
    handle @robot {
        rewrite * /xiaozhi/ws
        reverse_proxy UPSTREAM
    }
    handle {
        respond 404
    }
}
'''.replace('DOMAIN',domain).replace('PREFIX',prefix).replace('OTA_JSON',ota).replace('DEVICE_ID',re.escape(device)).replace('UPSTREAM',upstream)
(args.output/'Caddyfile').write_text(config)
print('Private gateway files generated; device URLs are not printed')
