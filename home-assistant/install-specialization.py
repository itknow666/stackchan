import json, subprocess
from pathlib import Path
base=Path(__file__).resolve().parent
files={'custom_components/dania_setup/'+p.name:p.read_text(encoding='utf-8') for p in (base/'dania_setup').glob('*') if p.is_file()}
files['dania-local.json']=(base/'local-config.json').read_text(encoding='utf-8')
script='import pathlib, json, os\nroot=pathlib.Path("/opt/stackchan-homeassistant/config")\nfiles=json.loads('+repr(json.dumps(files,ensure_ascii=False))+')\n'
script+='''for name,data in files.items():
 p=root/name
 p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(data,encoding='utf-8')
 os.chmod(p,0o600)
p=root/'configuration.yaml'
data=p.read_text()
if 'dania_setup:' not in data: p.write_text(data+'\\ndania_setup:\\n')
print('SPECIALIZATION_INSTALLED')
'''
r=subprocess.run(['wsl','-d','Ubuntu-22.04','-u','root','--','python3','-'],input=script.encode(),capture_output=True)
print(r.stdout.decode(errors='replace'))
if r.returncode: raise RuntimeError(r.stderr.decode(errors='replace'))
