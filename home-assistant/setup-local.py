import json, secrets, subprocess
from pathlib import Path

base = Path(__file__).resolve().parent
cfgpath = base/'local-config.json'
if not cfgpath.exists():
    cfgpath.write_text(json.dumps({'mqtt_host':'127.0.0.1','mqtt_port':1883,'mqtt_user':'stackchan','mqtt_password':secrets.token_urlsafe(32)},indent=2),encoding='utf-8')
cfg = json.loads(cfgpath.read_text(encoding='utf-8'))
script = '''set -e
install -d -m 700 /opt/stackchan-homeassistant/config
cat > /etc/mosquitto/conf.d/stackchan.conf <<'EOF'
listener 1883 127.0.0.1
allow_anonymous false
password_file /etc/mosquitto/stackchan-passwords
EOF
mosquitto_passwd -b -c /etc/mosquitto/stackchan-passwords stackchan PASSWORD
chown root:mosquitto /etc/mosquitto/stackchan-passwords
chmod 640 /etc/mosquitto/stackchan-passwords
systemctl enable mosquitto
systemctl restart mosquitto
if [ ! -e /opt/stackchan-homeassistant/config/configuration.yaml ]; then
cat > /opt/stackchan-homeassistant/config/configuration.yaml <<'EOF'
default_config:
homeassistant:
  name: 达妮娅的家
  time_zone: Asia/Hong_Kong
http:
  server_host: 127.0.0.1
lovelace:
  dashboards:
    stackchan-control:
      mode: yaml
      title: 达妮娅控制台
      icon: mdi:robot-happy
      show_in_sidebar: true
      filename: stackchan-dashboard.yaml
EOF
fi
if ! docker container inspect stackchan-homeassistant >/dev/null 2>&1; then
docker run -d --name stackchan-homeassistant --restart unless-stopped --network host -e TZ=Asia/Hong_Kong -v /opt/stackchan-homeassistant/config:/config ghcr.io/home-assistant/home-assistant:2026.11.1
fi
docker start stackchan-homeassistant >/dev/null
echo HOME_ASSISTANT_STARTED
'''.replace('PASSWORD',cfg['mqtt_password'])
r = subprocess.run(['wsl','-d','Ubuntu-22.04','-u','root','--','bash','-s'],input=script.encode(),capture_output=True)
print(r.stdout.decode(errors='replace'))
if r.returncode:
    print(r.stderr.decode(errors='replace'))
    raise SystemExit(r.returncode)
