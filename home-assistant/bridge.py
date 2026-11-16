"""Windows BLE + recorded-voice bridge using Home Assistant MQTT discovery."""
import asyncio, json, logging, math, re, subprocess, time, wave, winsound
from pathlib import Path
import paho.mqtt.client as mqtt
from bleak import BleakClient, BleakScanner

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
TOPIC = 'stackchan_dania'
UUID = 'e2e5e5e{}-1234-5678-1234-56789abcdef0'
SAFE_ID=re.compile(r'[A-Za-z0-9_-]{1,64}\Z')
PC_ACTIONS={'open_app':'打开指定软件','open_project':'打开项目文件夹','set_volume':'电脑音量',
            'media':'媒体控制','search_web':'网页搜索','lock':'锁定电脑'}

def pc_status(path,now=None):
    """Read only a bounded, credential-free projection of the companion state."""
    offline={'online':False,'mode':'未知','ready':False,'actions':'无','apps':'无','projects':'无'}
    try:
        if path.stat().st_size>32768: return offline
        raw=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(raw,dict): return offline
        now=time.time()*1000 if now is None else now
        stamp=raw.get('updated_at_ms')
        if (raw.get('version')!=1 or type(stamp)!=int or not 0<=now-stamp<=15000 or
            type(raw.get('connected'))!=bool or type(raw.get('dry_run'))!=bool): return offline
        caps=raw.get('capabilities',{})
        if not isinstance(caps,dict): return offline
        actions=caps.get('actions',[])
        if not isinstance(actions,list) or len(actions)>6 or any(a not in PC_ACTIONS for a in actions): return offline
        def names(group):
            entries=caps.get(group,[])
            if not isinstance(entries,list) or len(entries)>64: raise ValueError('Invalid capability list')
            values=[]
            for entry in entries:
                name=entry.get('name') if isinstance(entry,dict) else None
                if not isinstance(name,str) or not 1<=len(name)<=100 or not name.isprintable(): raise ValueError('Invalid capability name')
                values.append(name)
            return '、'.join(values) or '无'
        return {'online':raw['connected'],'mode':'模拟' if raw['dry_run'] else '真实执行',
                'ready':raw['connected'] and not raw['dry_run'] and bool(actions),
                'actions':'、'.join(PC_ACTIONS[a] for a in actions) or '无',
                'apps':names('apps'),'projects':names('projects')}
    except (OSError,ValueError,TypeError): return offline
FACES = {
    '默认': {'leftEye':{'weight':100,'rotation':0},'rightEye':{'weight':100,'rotation':0},'mouth':{'weight':0}},
    '微笑': {'leftEye':{'weight':72,'rotation':1550},'rightEye':{'weight':72,'rotation':-1550},'mouth':{'weight':35}},
    '困倦': {'leftEye':{'weight':20,'rotation':0},'rightEye':{'weight':20,'rotation':0},'mouth':{'weight':0}},
    '惊讶': {'leftEye':{'weight':100,'rotation':0},'rightEye':{'weight':100,'rotation':0},'mouth':{'weight':90}},
}

def ssh(script, cfg):
    cloud=cfg.get('cloud', {})
    if not cloud.get('enabled', False):
        raise RuntimeError('云端管理未配置，请先填写本地配置的 cloud 部分')
    args=['ssh','-i',str(Path(cloud['identity_file']).expanduser()),
          '-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
          '-o','UserKnownHostsFile='+str(Path(cloud['known_hosts_file']).expanduser()),
          '-o','ConnectTimeout=8',cloud['user']+'@'+cloud['host'],'python3 -']
    r=subprocess.run(args,input=script,text=True,capture_output=True,timeout=18,
                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if r.returncode: raise RuntimeError('云端 SSH 操作失败')
    return json.loads(r.stdout)

def cloud_status(enabled=None, cfg=None):
    if cfg is None: cfg=json.loads((BASE/'local-config.json').read_text(encoding='utf-8'))
    cloud=cfg.get('cloud', {})
    manifest=cloud.get('manifest_path','/opt/stackchan/data/voice-pack/manifest.json')
    service=cloud.get('service','stackchan')
    script="import json, pathlib, subprocess, os\np=pathlib.Path("+repr(manifest)+")\nd=json.loads(p.read_text())\n"
    if enabled is not None:
        script += f"d['enabled']={bool(enabled)!r}\nt=p.with_suffix('.tmp')\nt.write_text(json.dumps(d,ensure_ascii=False))\nos.chown(t,p.stat().st_uid,p.stat().st_gid)\nos.chmod(t,0o600)\nt.replace(p)\n"
    script += "print(json.dumps({'enabled':d['enabled'],'clips':len(d['rules']),'service':subprocess.run(['systemctl','is-active',"+repr(service)+"],capture_output=True,text=True).stdout.strip()}))\n"
    return ssh(script,cfg)

def motion_payload(axis, value):
    if axis not in ('yaw','pitch'): raise ValueError('未知舵机轴')
    value=float(value)
    lo,hi=(-90,90) if axis=='yaw' else (5,85)
    if not math.isfinite(value) or not lo<=value<=hi: raise ValueError('角度超出范围')
    return {('yawServo' if axis=='yaw' else 'pitchServo'):{'angle':round(value*10),'speed':20}}

def volume_value(value):
    value=float(value)
    if not math.isfinite(value) or not value.is_integer() or not 0<=value<=100:
        raise ValueError('音量须为零到一百之间的整数')
    return int(value)

class Bridge:
    def __init__(self, cfg=None):
        self.cfg=cfg if cfg is not None else json.loads((BASE/'local-config.json').read_text(encoding='utf-8'))
        self.pack_dir=(BASE/self.cfg.get('voice_pack_dir','voice-pack')).resolve()
        manifest=self.pack_dir/'manifest.json'
        self.pack=json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else {'rules':[]}
        self.rules={Path(r['file']).stem:r for r in self.pack['rules']}
        self.client=None
        self.connect_lock=asyncio.Lock()
        self.config_lock=asyncio.Lock()
        self.config_request=time.time_ns()%2147483646
        self.robot_preview_task=None
        self.robot_preview_cancel=asyncio.Event()
        self.auto_connect=True
        self.queue=asyncio.Queue(maxsize=16)
        self.last_command=0
        self.mqtt=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id='stackchan-dania-windows')
        self.mqtt.username_pw_set(self.cfg['mqtt_user'],self.cfg['mqtt_password'])
        self.mqtt.will_set(f'{TOPIC}/availability','offline',retain=True)
        self.mqtt.on_connect=self.on_connect
        self.mqtt.on_message=self.on_message

    def publish(self,topic,value):
        if isinstance(value,dict): value=json.dumps(value,ensure_ascii=False)
        self.mqtt.publish(f'{TOPIC}/{topic}',value,qos=1,retain=True)

    def on_connect(self,client,userdata,flags,reason,properties):
        if reason.is_failure: return
        client.subscribe(f'{TOPIC}/command/+',qos=1)
        self.discovery()
        self.publish('availability','online')
        self.publish('ble','ON' if self.client and self.client.is_connected else 'OFF')
        self.publish('volume_available','OFF')
        self.storage_unavailable('未知')
        self.publish('yaw',0)
        self.publish('pitch',45)
        self.publish('last_result','电脑桥接已连接；AI Agent 模式下会自动连接蓝牙')

    def on_message(self,client,userdata,msg):
        # Never replay stale physical actions retained by another publisher.
        if msg.retain or len(msg.payload)>512: return
        try:
            payload=msg.payload.decode('utf-8')
            command=msg.topic.rsplit('/',1)[-1]
            self.loop.call_soon_threadsafe(self.enqueue,command,payload)
        except (UnicodeError,RuntimeError): pass

    def enqueue(self,command,payload):
        try: self.queue.put_nowait((command,payload))
        except asyncio.QueueFull: self.publish('last_result','操作过快，请稍后再试')

    def discovery(self):
        device={'identifiers':['stackchan_dania_local'],'name':'达妮娅 StackChan','manufacturer':'M5Stack + 本地定制','model':'K151 / DeepSeek / BLE'}
        def add(kind,key,name,extra,ble=False):
            conf={'name':name,'unique_id':f'dania_{key}','default_entity_id':f'{kind}.dania_{key}',
                  'device':device,'availability':[{'topic':f'{TOPIC}/availability'}],**extra}
            if ble:
                conf['availability'].append({'topic':f'{TOPIC}/ble','payload_available':'ON','payload_not_available':'OFF'})
                conf['availability_mode']='all'
            self.mqtt.publish(f'homeassistant/{kind}/dania_{key}/config',json.dumps(conf,ensure_ascii=False),qos=1,retain=True)
        add('binary_sensor','bluetooth','蓝牙连接',{'state_topic':f'{TOPIC}/ble','device_class':'connectivity'})
        add('binary_sensor','cloud','DeepSeek 云端服务',{'state_topic':f'{TOPIC}/cloud','device_class':'connectivity'})
        add('sensor','result','最近操作',{'state_topic':f'{TOPIC}/last_result','icon':'mdi:message-text'})
        add('sensor','clips','录音数量',{'state_topic':f'{TOPIC}/clips','icon':'mdi:playlist-music'})
        for key,name in [('storage_status','SD 状态'),('storage_pack','SD 资源包'),('storage_clips','SD 录音数'),('storage_free','SD 剩余容量'),('storage_total','SD 总容量')]:
            extra={'state_topic':f'{TOPIC}/{key}','icon':'mdi:micro-sd'}
            if key in ('storage_free','storage_total'): extra['unit_of_measurement']='MiB'
            add('sensor',key,name,extra,True)
        add('binary_sensor','pc_online','电脑助手在线',{'state_topic':f'{TOPIC}/pc_online','device_class':'connectivity'})
        add('binary_sensor','pc_ready','电脑真实执行就绪',{'state_topic':f'{TOPIC}/pc_ready'})
        for key,name in [('pc_mode','电脑执行模式'),('pc_actions','允许的电脑操作'),('pc_apps','允许打开的软件'),('pc_projects','允许打开的项目')]:
            add('sensor',key,name,{'state_topic':f'{TOPIC}/{key}'})
        add('button','stop_local_clip','停止机器人试听',{'command_topic':f'{TOPIC}/command/stop_robot_preview','icon':'mdi:stop-circle'},True)
        add('button','connect','连接蓝牙',{'command_topic':f'{TOPIC}/command/connect','icon':'mdi:bluetooth-connect'})
        add('button','disconnect','断开蓝牙',{'command_topic':f'{TOPIC}/command/disconnect','icon':'mdi:bluetooth-off'})
        for axis,name,lo,hi in [('yaw','左右转头目标',-90,90),('pitch','抬头低头目标',5,85)]:
            add('number',axis,name,{'command_topic':f'{TOPIC}/command/{axis}','state_topic':f'{TOPIC}/{axis}','min':lo,'max':hi,'step':5,'unit_of_measurement':'°','mode':'slider'},True)
        add('number','volume','机器人音量',{
            'command_topic':f'{TOPIC}/command/volume','state_topic':f'{TOPIC}/volume',
            'min':0,'max':100,'step':1,'unit_of_measurement':'%','mode':'slider','icon':'mdi:volume-high',
            'availability_mode':'all','availability':[
                {'topic':f'{TOPIC}/availability'},
                {'topic':f'{TOPIC}/ble','payload_available':'ON','payload_not_available':'OFF'},
                {'topic':f'{TOPIC}/volume_available','payload_available':'ON','payload_not_available':'OFF'}]})
        add('select','face','脸部形状',{'command_topic':f'{TOPIC}/command/face','state_topic':f'{TOPIC}/face','options':list(FACES)},True)
        add('light','rgb','两侧灯光',{'schema':'json','command_topic':f'{TOPIC}/command/rgb','state_topic':f'{TOPIC}/rgb','supported_color_modes':['rgb']},True)
        add('switch','voice_pack','达妮娅录音触发',{'command_topic':f'{TOPIC}/command/voice_pack','state_topic':f'{TOPIC}/voice_pack','icon':'mdi:account-voice'})
        for key,rule in self.rules.items():
            add('button','preview_'+key,'电脑试听：'+rule['triggers'][0],{'command_topic':f'{TOPIC}/command/preview','payload_press':key,'icon':'mdi:volume-high'})
            if SAFE_ID.fullmatch(rule.get('id',key)):
                add('button','robot_preview_'+key,'机器人 SD 试听：'+rule['triggers'][0],{'command_topic':f'{TOPIC}/command/robot_preview','payload_press':key,'icon':'mdi:robot'},True)
        self.publish('clips',len(self.rules))

    async def connect(self):
        async with self.connect_lock:
            await self._connect()

    async def _connect(self):
        if self.client and self.client.is_connected: return
        found=await BleakScanner.discover(timeout=10,return_adv=True)
        candidates=[d for d,a in found.values() if 'stack' in (d.name or a.local_name or '').lower() or any(u.startswith('e2e5e5') for u in a.service_uuids)]
        if len(candidates)!=1: raise RuntimeError('未找到唯一的 StackChan，请保持 AI Agent 开启、靠近电脑，并断开手机蓝牙连接')
        def disconnected(_):
            self.publish('ble','OFF')
            self.publish('volume_available','OFF')
            self.storage_unavailable('蓝牙已断开')
            self.robot_preview_cancel.set()
        c=BleakClient(candidates[0],timeout=20,disconnected_callback=disconnected)
        await c.connect()
        for suffix in ('1','2','3','4'):
            if not c.services.get_characteristic(UUID.format(suffix)):
                await c.disconnect()
                raise RuntimeError('固件蓝牙接口不匹配')
        self.client=c
        self.publish('ble','ON')
        (BASE/'ble-device.json').write_text(json.dumps({'address':candidates[0].address,'name':candidates[0].name}),encoding='utf-8')

    async def write(self,suffix,data):
        if not self.client or not self.client.is_connected:
            if self.auto_connect: await self.connect()
            else: raise RuntimeError('蓝牙已手动断开，请先点击连接蓝牙')
        # The firmware accepts complete JSON writes. Windows long GATT writes
        # negotiate MTU, so no assumption about the new fragmentation extension.
        payload=json.dumps(data,separators=(',',':')).encode()
        await self.client.write_gatt_char(UUID.format(suffix),payload,response=True)

    def next_request(self):
        self.config_request=self.config_request%2147483647+1
        return self.config_request

    async def config_reply(self,request,kind,timeout=5):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            await asyncio.sleep(.1)
            if hasattr(self.client,'is_connected') and not self.client.is_connected:
                raise RuntimeError('蓝牙已断开')
            raw=await self.client.read_gatt_char(UUID.format('3'))
            if len(raw)>4096: continue
            try: reply=json.loads(raw)
            except (ValueError,UnicodeError): continue
            if (isinstance(reply,dict) and reply.get('cmd')==kind and
                type(reply.get('request_id'))==int and reply['request_id']==request): return reply
        raise TimeoutError('机器人未返回匹配的确认')

    async def volume_exchange(self,value=None):
        if value is not None: value=volume_value(value)
        async with self.config_lock:
            request=self.next_request()
            data={'cmd':'getVolume' if value is None else 'setVolume','request_id':request}
            if value is not None: data['value']=value
            await self.write('3',data)
            try:
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    reply=await self.config_reply(request,'volume',max(.01,deadline-time.monotonic()))
                    if type(reply.get('value'))!=int or not 0<=reply['value']<=100: continue
                    if value is not None and reply['value']!=value: continue
                    self.publish('volume',reply['value'])
                    self.publish('volume_available','ON')
                    return reply['value']
            except TimeoutError: pass
            self.publish('volume_available','OFF')
            raise RuntimeError('机器人未确认音量，请确认使用支持音量控制的定制固件')

    def storage_unavailable(self,status):
        self.publish('storage_status',status)
        self.publish('storage_pack','未知')
        for key in ('storage_clips','storage_free','storage_total'): self.publish(key,'unknown')

    async def storage_exchange(self):
        async with self.config_lock:
            request=self.next_request()
            await self.write('3',{'cmd':'getStorageInfo','request_id':request})
            try: reply=await self.config_reply(request,'storage',2)
            except TimeoutError:
                self.storage_unavailable('未知或固件不支持');return None
            if (type(reply.get('mounted'))!=bool or type(reply.get('valid'))!=bool or
                any(type(reply.get(k))!=int or not 0<=reply[k]<=1048576 for k in ('clips','total_mb','free_mb')) or
                reply['clips']>64 or reply['free_mb']>reply['total_mb'] or
                not isinstance(reply.get('pack_id'),str) or
                (reply['valid'] and not SAFE_ID.fullmatch(reply['pack_id']))):
                self.storage_unavailable('响应无效');return None
            self.publish('storage_status','资源包有效' if reply['valid'] else ('已挂载，资源不可用' if reply['mounted'] else '未挂载'))
            self.publish('storage_pack',reply['pack_id'] if reply['valid'] else '无')
            for target,source in [('storage_clips','clips'),('storage_free','free_mb'),('storage_total','total_mb')]: self.publish(target,reply[source])
            return reply

    async def robot_preview(self,key):
        request=None
        terminal=False
        def report(state,cancel_requested):
            messages={'unavailable':'机器人未开始试听：请保持待机并检查 SD 资源包',
                      'failed':'机器人试听中断','cancelled':'机器人试听已停止',
                      'completed':'机器人录音已播放结束；停止未确认' if cancel_requested else '机器人 SD 试听已播放完成'}
            self.publish('last_result',messages[state])
        try:
            clip=self.rules[key].get('id',key)
            async with self.config_lock:
                request=self.next_request()
                cancel_sent=False
                try:
                    await self.write('3',{'cmd':'playLocalClip','id':clip,'request_id':request})
                    deadline=time.monotonic()+22
                    last_state=None
                    while time.monotonic()<deadline:
                        if self.robot_preview_cancel.is_set() and not cancel_sent:
                            await self.write('3',{'cmd':'cancelLocalClip','request_id':request})
                            cancel_sent=True
                            self.publish('last_result','正在等待机器人停止确认')
                        # Never write a status query while a physical command
                        # may be pending in the firmware's latest-only mailbox.
                        # Notifications update this same readable GATT cache.
                        try: reply=await self.config_reply(request,'local_clip',min(.3,max(.01,deadline-time.monotonic())))
                        except TimeoutError: continue
                        state=reply.get('state')
                        if state in ('completed','unavailable','failed','cancelled'):
                            terminal=True
                            report(state,cancel_sent)
                            return
                        if state not in ('pending','accepted'): raise ValueError('无效机器人试听状态')
                        if state!=last_state and not cancel_sent:
                            self.publish('last_result','机器人正在播放 SD 录音' if state=='accepted' else '机器人已收到试听请求，等待播放')
                        last_state=state
                        await asyncio.sleep(.1)
                    raise TimeoutError('机器人试听结果未知，未重复播放')
                finally:
                    if not terminal and self.client and self.client.is_connected:
                        # At most one extra idempotent cancel. Retain the lock
                        # while reading its terminal ACK so volume/storage
                        # polling cannot immediately overwrite the cancellation.
                        try:
                            await self.write('3',{'cmd':'cancelLocalClip','request_id':request})
                            cleanup_deadline=time.monotonic()+2
                            while time.monotonic()<cleanup_deadline:
                                try: reply=await self.config_reply(request,'local_clip',min(.3,max(.01,cleanup_deadline-time.monotonic())))
                                except TimeoutError: continue
                                if reply.get('state') in ('completed','unavailable','failed','cancelled'):
                                    terminal=True
                                    report(reply['state'],True)
                                    break
                            if not terminal: self.publish('last_result','机器人试听及停止结果未知，未重复播放')
                        except Exception: pass
        except asyncio.CancelledError: raise
        except Exception:
            if not terminal: self.publish('last_result','机器人试听未确认或已中断，未重复播放')

    def preview_file(self,key):
        name=self.rules[key]['file']
        path=(self.pack_dir/name).resolve()
        if Path(name).name!=name or path.parent!=self.pack_dir or path.suffix!='.pcm':
            raise ValueError('无效录音路径')
        return path

    def preview(self,key):
        if key not in self.rules: raise ValueError('未知录音')
        path=BASE/'preview.wav'
        with wave.open(str(path),'wb') as out:
            out.setparams((1,2,24000,0,'NONE','not compressed'))
            out.writeframes(self.preview_file(key).read_bytes())
        winsound.PlaySound(str(path),winsound.SND_FILENAME|winsound.SND_ASYNC)

    async def command(self,key,payload):
        if key=='connect':
            self.auto_connect=True
            await self.connect()
        elif key=='disconnect':
            self.auto_connect=False
            async with self.connect_lock:
                if self.client: await self.client.disconnect()
            self.publish('ble','OFF')
        elif key in ('yaw','pitch'):
            await self.write('1',motion_payload(key,payload))
            self.publish(key,float(payload))
        elif key=='volume':
            await self.volume_exchange(volume_value(payload))
        elif key=='face':
            if payload not in FACES: raise ValueError('未知脸部形状')
            await self.write('2',FACES[payload]); self.publish(key,payload)
        elif key=='rgb':
            d=json.loads(payload)
            color=d.get('color',{'r':128,'g':0,'b':255})
            rgb=[color[k] for k in ('r','g','b')]
            if any(type(v)!=int or not 0<=v<=255 for v in rgb): raise ValueError('无效灯光颜色')
            colorhex='#'+''.join(f'{v:02X}' for v in rgb) if d.get('state')=='ON' else '#000000'
            await self.write('4',{'leftRgbColor':colorhex,'rightRgbColor':colorhex})
            self.publish('rgb',{'state':d.get('state','OFF'),'color_mode':'rgb','color':color})
        elif key=='voice_pack':
            if payload not in ('ON','OFF'): raise ValueError('无效开关值')
            status=await asyncio.to_thread(cloud_status,payload=='ON',self.cfg)
            self.publish('voice_pack','ON' if status['enabled'] else 'OFF')
        elif key=='preview':
            self.preview(payload)
        elif key=='robot_preview':
            if payload not in self.rules or not SAFE_ID.fullmatch(self.rules[payload].get('id',payload)):
                raise ValueError('未知机器人录音')
            if self.robot_preview_task and not self.robot_preview_task.done(): raise ValueError('机器人试听进行中')
            self.robot_preview_cancel.clear()
            self.robot_preview_task=asyncio.create_task(self.robot_preview(payload))
        elif key=='stop_robot_preview':
            if payload not in ('','PRESS'): raise ValueError('无效停止操作')
            self.robot_preview_cancel.set()
        else: raise ValueError('未知操作')
        if key=='preview': result='电脑正在试听：'+self.rules[payload]['triggers'][0]
        elif key=='robot_preview': result='正在请求机器人 SD 试听，等待实际确认'
        elif key=='stop_robot_preview': result='已请求停止机器人试听，等待实际确认'
        elif key=='voice_pack': result='达妮娅录音触发已'+('开启' if payload=='ON' else '关闭；恢复 DeepSeek 语音')
        elif key=='connect': result='蓝牙已连接；可同时使用 AI Agent 聊天与遥控'
        elif key=='disconnect': result='蓝牙已断开并暂停自动重连；AI Agent 聊天照常使用'
        elif key=='volume': result='机器人音量已设为'+str(volume_value(payload))+'%'+('（静音）' if volume_value(payload)==0 else '')
        else: result='控制指令已发送，请在机器人上确认效果'
        self.publish('last_result',result)

    async def poll_cloud(self):
        if not self.cfg.get('cloud',{}).get('enabled',False):
            self.publish('cloud','OFF')
            self.publish('voice_pack','OFF')
            return
        while True:
            try:
                status=await asyncio.to_thread(cloud_status,None,self.cfg)
                self.publish('cloud','ON' if status['service']=='active' else 'OFF')
                self.publish('voice_pack','ON' if status['enabled'] else 'OFF')
                self.publish('clips',status['clips'])
            except Exception:
                self.publish('cloud','OFF')
                logging.warning('Cloud status unavailable')
            await asyncio.sleep(60)

    async def keep_bluetooth(self):
        while True:
            if self.auto_connect and not (self.client and self.client.is_connected):
                try:
                    await self.connect()
                    self.publish('last_result','蓝牙已自动连接；AI Agent 聊天与遥控可以同时使用')
                except Exception:
                    # A powered-off or distant robot is normal; retry quietly.
                    pass
            await asyncio.sleep(20)

    async def poll_volume(self):
        while True:
            if not (self.client and self.client.is_connected):
                await asyncio.sleep(1)
                continue
            try: await self.volume_exchange()
            except Exception:
                self.publish('volume_available','OFF')
            try: await self.storage_exchange()
            except Exception: self.storage_unavailable('蓝牙不可用')
            await asyncio.sleep(15)

    async def poll_pc(self):
        config=self.cfg.get('pc_assistant',{})
        path=config.get('status_file') if isinstance(config,dict) else None
        while True:
            status={'online':False,'mode':'未配置','ready':False,'actions':'无','apps':'无','projects':'无'}
            if isinstance(path,str) and path:
                try: status=await asyncio.to_thread(pc_status,(BASE/Path(path)).resolve())
                except (ValueError,OSError): pass
            self.publish('pc_online','ON' if status['online'] else 'OFF')
            self.publish('pc_ready','ON' if status['ready'] else 'OFF')
            for key in ('mode','actions','apps','projects'): self.publish('pc_'+key,status[key])
            await asyncio.sleep(5)

    async def run(self):
        self.loop=asyncio.get_running_loop()
        self.mqtt.connect_async(self.cfg['mqtt_host'],self.cfg['mqtt_port'],30)
        self.mqtt.loop_start()
        poll=asyncio.create_task(self.poll_cloud())
        bluetooth=asyncio.create_task(self.keep_bluetooth())
        volume=asyncio.create_task(self.poll_volume())
        pc=asyncio.create_task(self.poll_pc())
        try:
            while True:
                key,payload=await self.queue.get()
                delay=max(0,0.25-(time.monotonic()-self.last_command))
                await asyncio.sleep(delay)
                try: await self.command(key,payload)
                except Exception as exc:
                    self.publish('last_result',str(exc)[:180])
                    logging.warning('Command %s failed: %s',key,type(exc).__name__)
                self.last_command=time.monotonic()
        finally:
            poll.cancel()
            bluetooth.cancel()
            volume.cancel()
            pc.cancel()
            if self.robot_preview_task:
                self.robot_preview_task.cancel()
                await asyncio.gather(self.robot_preview_task,return_exceptions=True)
            if self.client and self.client.is_connected: await self.client.disconnect()
            self.publish('availability','offline')
            self.mqtt.disconnect(); self.mqtt.loop_stop()

if __name__=='__main__':
    logging.basicConfig(filename=BASE/'bridge.log',level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    asyncio.run(Bridge().run())
