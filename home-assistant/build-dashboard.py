import argparse, json, subprocess
from pathlib import Path
base=Path(__file__).resolve().parent
parser=argparse.ArgumentParser()
parser.add_argument('--manifest',type=Path,default=base/'voice-pack/manifest.json')
parser.add_argument('--output',type=Path,default=base/'stackchan-dashboard.yaml')
parser.add_argument('--install',action='store_true',help='Install into the dedicated Ubuntu-22.04 WSL container configuration')
args=parser.parse_args()
pack=json.loads(args.manifest.read_text(encoding='utf-8')) if args.manifest.exists() else {'rules':[]}
cards=[
 {'type':'markdown','content':'# 达妮娅控制台\n\n**聊天与遥控同时使用：**机器人保持 AI Agent，电脑会自动连接蓝牙，可直接控制音量、动作、脸和灯光，无须切换 DANCE。手动控制六秒内优先，语音聊天继续运行。\n\n**机器人音量：**下方滑块调节机器人扬声器，零为静音，设置会保留到下次开机。蓝牙连接后会读取当前音量。\n\n**离开电脑：**Wi-Fi 聊天使用云端，摸头录音需自行导入。当前独立供电冷启动仍待实机复测。点击“断开蓝牙”会暂停自动重连，点击“连接蓝牙”可恢复。'},
 {'type':'entities','title':'连接与语音','entities':['binary_sensor.dania_bluetooth','binary_sensor.dania_cloud','number.dania_volume','switch.dania_voice_pack','sensor.dania_clips','sensor.dania_result']},
 {'type':'horizontal-stack','cards':[{'type':'button','entity':'button.dania_connect','name':'连接蓝牙','tap_action':{'action':'perform-action','perform_action':'button.press','target':{'entity_id':'button.dania_connect'}}},{'type':'button','entity':'button.dania_disconnect','name':'断开蓝牙','tap_action':{'action':'perform-action','perform_action':'button.press','target':{'entity_id':'button.dania_disconnect'}}}]},
 {'type':'entities','title':'蓝牙动作与脸部','entities':['number.dania_yaw','number.dania_pitch','select.dania_face','light.dania_rgb']},
 {'type':'markdown','title':'怎么触发录音','content':'在 AI Agent 中说完整的短句即可，例如“我回来了”“夸夸我”“给我加油”“我有点难过”。可在句首加“达妮娅”。其他问题继续由 DeepSeek 回答。\n\n下方按钮只在**电脑扬声器试听**，不会让机器人播放。'},
 {'type':'grid','title':'自定义录音 · 电脑试听','columns':3,'square':False,'cards':[
  {'type':'button','entity':'button.dania_preview_'+Path(r['file']).stem,'name':r['triggers'][0],'icon':'mdi:play-circle','tap_action':{'action':'perform-action','perform_action':'button.press','target':{'entity_id':'button.dania_preview_'+Path(r['file']).stem}}} for r in pack['rules']]},
 {'type':'markdown','title':'全部触发说法','content':'\n\n'.join('**'+r['triggers'][0]+'**：'+'、'.join(r['triggers']) for r in pack['rules'])},
]
dashboard={'title':'达妮娅','views':[{'title':'达妮娅','path':'dania','icon':'mdi:robot-happy','cards':cards}]}
data=json.dumps(dashboard,ensure_ascii=False,indent=2)
args.output.write_text(data,encoding='utf-8')
if args.install:
    script="from pathlib import Path\nPath('/opt/stackchan-homeassistant/config/stackchan-dashboard.yaml').write_text("+repr(data)+",encoding='utf-8')\n"
    r=subprocess.run(['wsl','-d','Ubuntu-22.04','-u','root','--','python3','-'],input=script.encode(),capture_output=True)
    if r.returncode: raise RuntimeError(r.stderr.decode(errors='replace'))
    print('DASHBOARD_INSTALLED')
else:
    print('DASHBOARD_GENERATED')
