import asyncio
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).parent))
from pc_state import read_pc_state

spec=importlib.util.spec_from_file_location('notifications',Path(__file__).with_name('codex_notifications.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class Notifications(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.status=self.root/'status.json';self.calls=[]
        self.cfg={'enabled':True,'ledger_file':str(self.root/'ledger.json'),
                  'clips':{'completed':'done','failed':'confused','cancelled':'see_you'}}
        self.task={'task_id':'a'*32,'state':'completed','finished_at_ms':100000}
        self.raw={'version':1,'connected':True,'dry_run':False,'updated_at_ms':100000,'codex_tasks':[self.task]}
        self.write();self.notifier=module.CompletionNotifier(self.cfg)
    def write(self):self.status.write_text(json.dumps(self.raw),encoding='utf-8')
    async def poll(self, connected, play, now):
        await self.notifier.poll(read_pc_state(self.status,now),connected,play,now)
    async def play(self,clip):self.calls.append(clip);return 'completed'

    async def test_connected_completion_once_across_restart(self):
        await self.poll(False,self.play,100100);self.assertFalse(self.calls)
        await self.poll(True,self.play,100100);self.assertEqual(self.calls,['done'])
        restored=module.CompletionNotifier(self.cfg)
        await restored.poll(read_pc_state(self.status,100100),True,self.play,100100);self.assertEqual(self.calls,['done'])

    async def test_stale_disconnected_simulated_do_not_play(self):
        for field,value in [('updated_at_ms',80000),('connected',False),('dry_run',True)]:
            original=self.raw[field];self.raw[field]=value;self.write()
            await self.poll(True,self.play,100100)
            self.assertFalse(self.calls);self.raw[field]=original

    async def test_busy_can_retry_but_unknown_never_replays(self):
        async def busy(clip):self.calls.append(clip);return 'unavailable'
        await self.poll(True,busy,100100)
        await self.poll(True,self.play,100200)
        self.assertEqual(self.calls,['done','done'])
        self.task['task_id']='b'*32;self.write()
        async def unknown(clip):self.calls.append(clip);raise TimeoutError
        await self.poll(True,unknown,100200)
        await module.CompletionNotifier(self.cfg).poll(read_pc_state(self.status,100300),True,self.play,100300)
        self.assertEqual(len(self.calls),3)

    async def test_mark_is_durable_before_physical_effect_and_failure_mapping(self):
        self.task['state']='failed';self.write()
        async def inspect(clip):
            ledger=json.loads((self.root/'ledger.json').read_text(encoding='utf-8'))
            self.assertEqual(ledger['a'*32]['state'],'attempted')
            self.assertEqual(clip,'confused');return 'completed'
        await self.poll(True,inspect,100200)

    async def test_expired_jobs_not_spoken_after_reconnect(self):
        self.raw['updated_at_ms']=300000;self.write()
        await self.poll(True,self.play,300001);self.assertFalse(self.calls)

    async def test_missing_or_malformed_status_does_not_announce(self):
        for text in ['[]', '{', '{"version":1}', 'x'*32769]:
            self.status.write_text(text,encoding='utf-8')
            await self.poll(True,self.play,100100)
        self.status.unlink()
        await self.poll(True,self.play,100100)
        self.assertFalse(self.calls)

if __name__=='__main__':unittest.main()
