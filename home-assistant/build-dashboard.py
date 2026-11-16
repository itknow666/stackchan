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
 {'type':'markdown','content':'# 达妮娅控制台\n\n**开机进入 AI Agent：**聊天和蓝牙遥控同时运行，手动动作约六秒内优先。进入 Agent 不自动检查或升级固件。底部上滑进入维护页，可调整网络、音量和关机。\n\n**机器人音量：**零为静音，设备确认后显示实际值并保留到下次开机。电脑音量由语音电脑工具单独控制。\n\n**离开电脑：**Wi-Fi 聊天继续使用云端，SD 摸头录音可离线播放。旧版独立供电、触摸与开机键问题已由用户确认修复；本次新固件的人工听感和维护操作仍待验收。蓝牙手动断开会暂停自动重连。'},
 {'type':'entities','title':'连接与语音','entities':['binary_sensor.dania_bluetooth','binary_sensor.dania_cloud','number.dania_volume','switch.dania_voice_pack','sensor.dania_clips','sensor.dania_result']},
 {'type':'entities','title':'SD 本地资源','entities':['sensor.dania_storage_status','sensor.dania_storage_pack','sensor.dania_storage_clips','sensor.dania_storage_free','sensor.dania_storage_total','button.dania_stop_local_clip']},
 {'type':'entities','title':'语音反向控制电脑','entities':['binary_sensor.dania_pc_online','binary_sensor.dania_pc_ready','sensor.dania_pc_mode','sensor.dania_pc_actions','sensor.dania_pc_apps','sensor.dania_pc_projects']},
 {'type':'horizontal-stack','cards':[{'type':'button','entity':'button.dania_connect','name':'连接蓝牙','tap_action':{'action':'perform-action','perform_action':'button.press','target':{'entity_id':'button.dania_connect'}}},{'type':'button','entity':'button.dania_disconnect','name':'断开蓝牙','tap_action':{'action':'perform-action','perform_action':'button.press','target':{'entity_id':'button.dania_disconnect'}}}]},
 {'type':'entities','title':'蓝牙动作与脸部','entities':['number.dania_yaw','number.dania_pitch','select.dania_face','light.dania_rgb']},
 {'type':'markdown','title':'怎么触发录音','content':'在 AI Agent 中说完整的短句，例如“我回来了”“夸夸我”“给我加油”“我有点难过”。句首可加“达妮娅”。匹配 SD 资源包时由机器人本地播放；没有兼容资源包时沿用云端录音，其他问题继续由 DeepSeek 回答。\n\n**电脑试听**使用电脑扬声器。**机器人 SD 试听**仅在机器人待机时播放，不打断聊天，完成状态以机器人实际播放排空回执为准；可点击停止按钮。'},
 {'type':'grid','title':'自定义录音 · 电脑试听','columns':3,'square':False,'cards':[
  {'type':'button','entity':'button.dania_preview_'+Path(r['file']).stem,'name':r['triggers'][0],'icon':'mdi:play-circle','tap_action':{'action':'perform-action','perform_action':'button.press','target':{'entity_id':'button.dania_preview_'+Path(r['file']).stem}}} for r in pack['rules']]},
 {'type':'grid','title':'自定义录音 · 机器人 SD 试听','columns':3,'square':False,'cards':[
  {'type':'button','entity':'button.dania_robot_preview_'+Path(r['file']).stem,'name':r['triggers'][0],'icon':'mdi:robot','tap_action':{'action':'perform-action','perform_action':'button.press','target':{'entity_id':'button.dania_robot_preview_'+Path(r['file']).stem}}} for r in pack['rules']]},
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
