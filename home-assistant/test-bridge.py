import asyncio, json, sys, threading, time, unittest, tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from bridge import Bridge, motion_payload, volume_value, TOPIC, cloud_status, UUID, pc_status
import paho.mqtt.client as mqtt

class Contracts(unittest.TestCase):
    def test_volume_range_and_device_acknowledgement(self):
        for raw,want in [('0',0),('100',100),('35.0',35)]: self.assertEqual(volume_value(raw),want)
        for raw in ('-1','101','35.5','nan','inf',''):
            with self.assertRaises(ValueError): volume_value(raw)
        b=Bridge({'mqtt_user':'test','mqtt_password':'test-only'})
        published=[]
        b.publish=lambda topic,value:published.append((topic,value))
        sent=[]
        async def write(suffix,data): sent.append((suffix,data))
        b.write=write
        class Device:
            reads=0
            async def read_gatt_char(self,uuid):
                self.assert_uuid=uuid
                self.reads+=1
                request=sent[-1][1]['request_id']
                return json.dumps({'cmd':'volume','value':36 if self.reads==2 else 35,
                    'request_id':request-1 if self.reads==1 else request}).encode()
        b.client=Device()
        self.assertEqual(asyncio.run(b.volume_exchange('35')),35)
        self.assertEqual(sent[0][0],'3')
        self.assertEqual(sent[0][1]['cmd'],'setVolume')
        self.assertEqual(sent[0][1]['value'],35)
        self.assertEqual(b.client.assert_uuid,UUID.format('3'))
        self.assertEqual(b.client.reads,3)
        self.assertIn(('volume',35),published)
        self.assertIn(('volume_available','ON'),published)
    def test_servo_units_and_limits(self):
        self.assertEqual(motion_payload('yaw',-30),{'yawServo':{'angle':-300,'speed':20}})
        self.assertEqual(motion_payload('pitch',45),{'pitchServo':{'angle':450,'speed':20}})
        for axis,value in [('yaw',91),('pitch',0),('pitch',90),('yaw','nan'),('pitch','inf'),('roll',10)]:
            with self.assertRaises(ValueError): motion_payload(axis,value)

    def test_recording_preview_cannot_escape_pack(self):
        with tempfile.TemporaryDirectory() as directory:
            b=Bridge({'mqtt_user':'test','mqtt_password':'test-only','voice_pack_dir':directory})
            self.assertEqual(b.rules,{})
            b.rules={'bad':{'file':'../private.pcm'},'good':{'file':'praise.pcm'}}
            with self.assertRaises(ValueError): b.preview_file('bad')
            self.assertEqual(b.preview_file('good').parent,Path(directory).resolve())

    def test_unknown_actions_rejected(self):
        b=Bridge({'mqtt_user':'test','mqtt_password':'test-only'})
        async def check():
            for key,payload in [('shell','whoami'),('face','unexpected'),('preview','../secret'),('voice_pack','1')]:
                with self.assertRaises(ValueError): await b.command(key,payload)
        asyncio.run(check())

    def test_retained_commands_not_replayed(self):
        b=Bridge({'mqtt_user':'test','mqtt_password':'test-only'})
        b.on_message(None,None,type('Message',(),{'retain':True,'payload':b'PRESS','topic':TOPIC+'/command/connect'})())
        self.assertTrue(b.queue.empty())

    def test_pc_status_is_fresh_and_never_exposes_private_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'status.json'
            raw={'version':1,'connected':True,'dry_run':False,'updated_at_ms':100000,
                 'endpoint':'PRIVATE_ENDPOINT','token':'PRIVATE_TOKEN','query':'PRIVATE_QUERY',
                 'capabilities':{'actions':['open_app','set_volume'],'apps':[{'id':'b','name':'浏览器','executable':'PRIVATE_PATH'}],'projects':[]}}
            path.write_text(json.dumps(raw),encoding='utf-8')
            status=pc_status(path,100100)
            self.assertTrue(status['online']);self.assertTrue(status['ready'])
            self.assertEqual(status['apps'],'浏览器')
            self.assertNotIn('PRIVATE',json.dumps(status))
            self.assertFalse(pc_status(path,116000)['online'])
            self.assertFalse(pc_status(path,99999)['online'])
            raw['dry_run']=True;path.write_text(json.dumps(raw),encoding='utf-8')
            self.assertFalse(pc_status(path,100100)['ready'])
            path.write_text('[]');self.assertFalse(pc_status(path,100100)['online'])

    def test_storage_and_volume_exchange_share_one_mailbox(self):
        b=Bridge({'mqtt_user':'test','mqtt_password':'test-only'});sent=[];published=[]
        b.publish=lambda *v:published.append(v)
        async def write(suffix,data): sent.append(data)
        b.write=write
        class Device:
            is_connected=True
            async def read_gatt_char(self,uuid):
                d=sent[-1]
                if d['cmd']=='getVolume': return json.dumps({'cmd':'volume','request_id':d['request_id'],'value':53}).encode()
                return json.dumps({'cmd':'storage','request_id':d['request_id'],'mounted':True,'valid':True,'pack_id':'test-pack','clips':31,'total_mb':29812,'free_mb':29811}).encode()
        b.client=Device()
        async def check():
            volume,storage=await asyncio.gather(b.volume_exchange(),b.storage_exchange())
            self.assertEqual(volume,53);self.assertEqual(storage['clips'],31)
        asyncio.run(check())
        self.assertEqual([d['cmd'] for d in sent],['getVolume','getStorageInfo'])
        self.assertNotEqual(sent[0]['request_id'],sent[1]['request_id'])
        self.assertIn(('storage_status','资源包有效'),published)

    def test_robot_preview_uses_status_and_completion_not_enqueue(self):
        b=Bridge({'mqtt_user':'test','mqtt_password':'test-only'});sent=[];published=[]
        b.rules={'hello':{'file':'hello.pcm','triggers':['你好']}}
        b.publish=lambda *v:published.append(v)
        async def write(suffix,data): sent.append(data)
        b.write=write
        class Device:
            is_connected=True
            reads=0
            async def read_gatt_char(self,uuid):
                self.reads+=1
                return json.dumps({'cmd':'local_clip','request_id':sent[-1]['request_id'],
                                   'state':['pending','accepted','completed'][min(self.reads-1,2)]}).encode()
        b.client=Device()
        asyncio.run(b.robot_preview('hello'))
        self.assertEqual(sent[0]['cmd'],'playLocalClip')
        self.assertEqual(sent[-1]['cmd'],'cancelLocalClip')
        self.assertEqual(sum(d['cmd']=='playLocalClip' for d in sent),1)
        self.assertIn(('last_result','机器人 SD 试听已播放完成'),published)

    def test_robot_preview_stop_does_not_replay_and_bad_ids_rejected(self):
        b=Bridge({'mqtt_user':'test','mqtt_password':'test-only'});b.rules={'hello':{'file':'hello.pcm'},'bad':{'id':'../secret'}}
        sent=[];published=[];b.publish=lambda *v:published.append(v)
        class Device:
            is_connected=True
            cancelled=False
            latest=None
            async def write(self,suffix,data):
                sent.append(data);self.latest=data
                def consume():
                    if self.latest is data and data['cmd']=='cancelLocalClip': self.cancelled=True
                asyncio.get_running_loop().call_later(.02,consume)
            async def read_gatt_char(self,uuid):
                return json.dumps({'cmd':'local_clip','request_id':sent[-1]['request_id'],'state':'cancelled' if self.cancelled else 'accepted'}).encode()
        b.client=Device();b.write=b.client.write;b.robot_preview_cancel.set()
        asyncio.run(b.robot_preview('hello'))
        self.assertEqual(sum(d['cmd']=='playLocalClip' for d in sent),1)
        self.assertTrue(any(d['cmd']=='cancelLocalClip' for d in sent))
        self.assertIn(('last_result','机器人试听已停止'),published)
        async def check():
            with self.assertRaises(ValueError): await b.command('robot_preview','bad')
        asyncio.run(check())

    def test_storage_old_firmware_and_disconnect_clear_state(self):
        b=Bridge({'mqtt_user':'test','mqtt_password':'test-only'});published=[]
        b.publish=lambda *v:published.append(v)
        async def write(*args): pass
        async def no_reply(*args): raise TimeoutError()
        b.write=write;b.config_reply=no_reply
        self.assertIsNone(asyncio.run(b.storage_exchange()))
        self.assertIn(('storage_status','未知或固件不支持'),published)
        self.assertIn(('storage_clips','unknown'),published)

def live():
    cfg=json.loads((Path(__file__).parent/'local-config.json').read_text())
    messages={}
    c=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id='stackchan-bridge-check')
    c.username_pw_set(cfg['mqtt_user'],cfg['mqtt_password'])
    c.on_connect=lambda client,*args:client.subscribe([(TOPIC+'/#',1),('homeassistant/+/+/config',1)])
    def receive(client,userdata,msg): messages[msg.topic]=msg.payload.decode()
    c.on_message=receive
    c.connect('127.0.0.1',1883); c.loop_start()
    time.sleep(3)
    c.disconnect(); c.loop_stop()
    assert messages.get(TOPIC+'/availability')=='online', 'bridge unavailable'
    assert messages.get(TOPIC+'/cloud')=='ON', 'cloud unavailable'
    discovery=[json.loads(v) for k,v in messages.items() if k.startswith('homeassistant/') and '/dania_' in k]
    assert len(discovery)>=12, 'Expected base HA entities'
    volume=messages.get(TOPIC+'/volume')
    assert messages.get(TOPIC+'/volume_available')=='ON', 'Device volume unavailable'
    assert volume is not None and 0<=int(volume)<=100, 'No confirmed robot volume'
    state=cloud_status()
    assert state['service']=='active'
    print('MQTT_BRIDGE_LIVE_AND_CLOUD_VOICE_PACK_OK')

if __name__=='__main__':
    result=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(Contracts))
    if not result.wasSuccessful(): sys.exit(1)
    if '--live' in sys.argv: live()
