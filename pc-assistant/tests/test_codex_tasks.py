import asyncio
import dataclasses
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dania_pc.config import Config, load_config
from dania_pc.codex_tasks import CodexTasks
from dania_pc.executor import NativeExecutor, validate_action


class Input:
    def __init__(self): self.value=b''
    def write(self,data): self.value+=data
    async def drain(self): pass
    def close(self): pass


class Process:
    def __init__(self, events, code=0, gate=None):
        self.stdin=Input(); self.stdout=self.lines(events,gate); self.returncode=None;self.code=code;self.pid=123
    async def lines(self,events,gate):
        if gate: await gate.wait()
        for event in events: yield json.dumps(event).encode()+b'\n'
    async def wait(self): self.returncode=self.code;return self.code
    def kill(self): self.returncode=-9


class Jobs(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.config=Config('wss://example.com/dania/pc/ws','x'*32,'desktop',frozenset({'codex_start','codex_status','codex_cancel'}),{},
            {'robot':{'name':'Robot','path':str(root)}},False,root/'status.json',
            {'executable':'C:/Codex/codex.exe','sandbox':'workspace-write','timeout_seconds':10,'tasks_file':root/'tasks.json'})
        self.calls=[];self.gate=None;self.events=[{'type':'thread.started','thread_id':'12345678-1234-1234-1234-123456789abc'},
            {'type':'item.completed','item':{'type':'agent_message','text':json.dumps({'outcome':'completed','summary':'完成了，测试通过。'},ensure_ascii=False)}},{'type':'turn.completed'}];self.code=0
        async def spawn(*args,**kwargs):
            self.calls.append((args,kwargs)); self.proc=Process(self.events,self.code,self.gate);return self.proc
        self.jobs=CodexTasks(self.config,spawn=spawn,job_factory=lambda pid: type("Job",(),{"close":lambda self:None})())
        async def terminate():
            if self.jobs.process: self.jobs.process.kill()
        self.jobs.terminate=terminate
        self.addAsyncCleanup(self.jobs.close)

    async def start(self): return await self.jobs.execute('codex_start',{'project':'robot','task':'检查项目'})

    async def test_accepted_success_resumes_and_no_shell(self):
        _, accepted=await self.start();self.assertTrue(accepted['accepted']);self.assertEqual(accepted['state'],'accepted')
        await self.jobs.worker
        _, result=await self.jobs.execute('codex_status',{'task_id':accepted['task_id']})
        self.assertEqual(result['state'],'completed');self.assertIn('测试通过',result['summary'])
        await self.start();await self.jobs.worker
        argv,opts=self.calls[-1];self.assertIn('resume',argv);self.assertNotIn('shell',opts)
        self.assertIn('sandbox_mode="workspace-write"',argv);self.assertIn('--ignore-user-config',argv)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox',argv)
        self.assertIn('Do not',self.proc.stdin.value.decode())
        self.assertNotIn('summary',self.jobs.projection()[0])
        restored=CodexTasks(self.config);self.assertEqual(len(restored.tasks),2);self.assertTrue(restored.sessions)

    async def test_failure_cannot_be_success(self):
        self.code=1
        await self.start();await self.jobs.worker
        self.assertEqual(self.jobs.tasks[-1]['state'],'failed')
        self.events=[{'type':'turn.failed','error':{'message':'PRIVATE_PATH'}}]
        await self.start();await self.jobs.worker
        self.assertEqual(self.jobs.tasks[-1]['state'],'failed')
        self.assertNotIn('PRIVATE',self.jobs.tasks[-1]['summary'])

    async def test_turn_completion_does_not_mean_goal_completed(self):
        for text in [json.dumps({'outcome':'blocked','summary':'读取被策略阻止，检查未完成。'}),
                     json.dumps({'outcome':'failed','summary':'测试失败。'}),
                     '工作完成了', '{bad json',
                     '{"outcome":"blocked","outcome":"completed","summary":"完成"}',
                     json.dumps({'outcome':'completed','summary':'完成','extra':True})]:
            self.events=[{'type':'item.completed','item':{'type':'agent_message','text':text}},
                         {'type':'turn.completed'}]
            await self.start();await self.jobs.worker
            self.assertEqual(self.jobs.tasks[-1]['state'],'failed')
        self.events=[{'type':'item.completed','item':{'type':'agent_message',
            'text':json.dumps({'outcome':'blocked','summary':'读取被策略阻止，检查未完成。'})}},{'type':'turn.completed'}]
        await self.start();await self.jobs.worker
        self.assertIn('未完成',self.jobs.tasks[-1]['summary'])
        self.assertEqual(self.jobs.schema_path.parent,self.jobs.path.parent)
        schema=json.loads(self.jobs.schema_path.read_text(encoding='utf-8'))
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(set(schema['required']),{'outcome','summary'})
        self.assertIn(str(self.jobs.schema_path),self.calls[-1][0])
        self.assertNotEqual(self.jobs.schema_path,self.jobs.path)

    async def test_busy_and_cancel_running(self):
        self.gate=asyncio.Event();await self.start()
        while self.jobs.process is None: await asyncio.sleep(0)
        _, accepted=await self.start();self.assertFalse(accepted['accepted']);self.assertEqual(len(self.calls),1)
        await self.jobs.execute('codex_cancel',{'task_id':'latest'});await self.jobs.worker
        self.assertEqual(self.jobs.tasks[-1]['state'],'cancelled')
        self.assertEqual(self.proc.returncode,-9)

    async def test_cancel_before_start(self):
        await self.start();await self.jobs.execute('codex_cancel',{'task_id':'latest'})
        await asyncio.gather(self.jobs.worker,return_exceptions=True)
        self.assertEqual(self.jobs.tasks[-1]['state'],'cancelled');self.assertFalse(self.calls)

    async def test_timeout_and_restart_never_replay(self):
        self.gate=asyncio.Event();self.config.codex['timeout_seconds']=.01
        await self.start();await self.jobs.worker
        self.assertEqual(self.jobs.tasks[-1]['state'],'timed_out')
        self.jobs.tasks[-1]['state']='running';self.jobs.save()
        restored=CodexTasks(self.config)
        self.assertEqual(restored.tasks[-1]['state'],'interrupted');self.assertIsNone(restored.worker)

    async def test_repeated_cancel_and_close_cannot_interrupt_cleanup(self):
        entered, released = asyncio.Event(), asyncio.Event()
        self.gate=asyncio.Event()
        async def terminate():
            entered.set()
            await released.wait()
            if self.jobs.process:self.jobs.process.kill()
        self.jobs.terminate=terminate
        await self.start()
        while self.jobs.process is None:await asyncio.sleep(0)
        await self.jobs.execute('codex_cancel',{'task_id':'latest'})
        await entered.wait()
        await self.jobs.execute('codex_cancel',{'task_id':'latest'})
        closing=asyncio.create_task(self.jobs.close())
        await asyncio.sleep(0)
        self.jobs.worker.cancel()
        await asyncio.sleep(0)
        self.assertFalse(self.jobs.worker.done())
        released.set()
        await closing
        self.assertEqual(self.jobs.tasks[-1]['state'],'cancelled')
        self.assertIsNone(self.jobs.process)
        persisted=json.loads(self.jobs.path.read_text(encoding='utf-8'))
        self.assertEqual(persisted['tasks'][-1]['state'],'cancelled')
        self.assertIn('finished_at_ms',persisted['tasks'][-1])

    async def test_repeated_cancel_during_spawn_still_joins_process(self):
        entered, released = asyncio.Event(), asyncio.Event()
        async def spawn(*args,**kwargs):
            entered.set()
            await released.wait()
            self.proc=Process([],0)
            return self.proc
        self.jobs.spawn=spawn
        await self.start();await entered.wait()
        await self.jobs.execute('codex_cancel',{'task_id':'latest'})
        await asyncio.sleep(0)
        self.jobs.worker.cancel();await asyncio.sleep(0)
        self.assertFalse(self.jobs.worker.done())
        released.set();await self.jobs.worker
        self.assertEqual(self.proc.returncode,-9)
        self.assertEqual(self.jobs.tasks[-1]['state'],'cancelled')
        self.assertIsNone(self.jobs.process)

    async def test_codex_home_is_explicit_child_env_only(self):
        import os
        home=Path(self.temp.name)/'auth-home'
        home.mkdir()
        self.config.codex['home']=home
        before=os.environ.get('CODEX_HOME')
        await self.start();await self.jobs.worker
        self.assertEqual(self.calls[0][1]['env']['CODEX_HOME'],str(home))
        self.assertEqual(os.environ.get('CODEX_HOME'),before)
        self.assertNotIn(str(home),self.calls[0][0])
        self.assertNotIn('CODEX_HOME',json.dumps(self.jobs.projection()))

    async def test_auth_home_change_does_not_resume_other_home_session(self):
        self.config.codex['home']=Path(self.temp.name)/'first-home'
        await self.start();await self.jobs.worker
        self.config.codex['home']=Path(self.temp.name)/'second-home'
        await self.start();await self.jobs.worker
        self.assertNotIn('resume',self.calls[-1][0])
        await self.start();await self.jobs.worker
        self.assertIn('resume',self.calls[-1][0])
        self.config.codex['home']=None
        with patch.dict('os.environ',{'CODEX_HOME':str(Path(self.temp.name)/'inherited-home')}):
            await self.start();await self.jobs.worker
        self.assertNotIn('resume',self.calls[-1][0])

    async def test_windows_sandbox_is_only_explicit_whitelisted_backend(self):
        argv=self.jobs.command(Path(self.temp.name))
        self.assertFalse(any(arg.startswith('windows.sandbox=') for arg in argv))
        for mode in ('mxc','elevated','unelevated'):
            self.config.codex['windows_sandbox']=mode
            self.assertIn('windows.sandbox="'+mode+'"',self.jobs.command(Path(self.temp.name)))
        sample=Path(__file__).parents[2]/'examples/pc-assistant.codex.example.json'
        raw=json.loads(sample.read_text(encoding='utf-8'))
        raw['token']='x'*32;raw['codex'].pop('home',None)
        config_path=Path(self.temp.name)/'config.json'
        for mode in ('danger-full-access','disabled','bypass',True,{}):
            raw['codex']['windows_sandbox']=mode
            config_path.write_text(json.dumps(raw),encoding='utf-8')
            with self.assertRaises(ValueError):load_config(config_path)

    async def test_config_enforces_sandbox_and_distinct_storage(self):
        sample=Path(__file__).parents[2]/'examples/pc-assistant.codex.example.json'
        raw=json.loads(sample.read_text(encoding='utf-8'))
        raw['token']='x'*32
        raw['codex'].pop('home',None)
        path=Path(self.temp.name)/'config.json'
        path.write_text(json.dumps(raw),encoding='utf-8')
        self.assertEqual(load_config(path).codex['sandbox'],'read-only')
        for sandbox in ['danger-full-access','unknown']:
            raw['codex']['sandbox']=sandbox;path.write_text(json.dumps(raw),encoding='utf-8')
            with self.assertRaises(ValueError):load_config(path)
        raw['codex']['sandbox']='workspace-write'
        raw['projects'][0]['path']=str(Path(self.temp.name)/'project')
        forbidden=Path(self.temp.name)/'project'/'auth';forbidden.mkdir(parents=True)
        raw['codex']['home']=str(forbidden)
        path.write_text(json.dumps(raw),encoding='utf-8')
        with self.assertRaises(ValueError):load_config(path)
        raw['codex'].pop('home')
        raw['codex']['tasks_file']='private/status.json'
        path.write_text(json.dumps(raw),encoding='utf-8')
        with self.assertRaises(ValueError):load_config(path)

    async def test_validation_and_dry_run(self):
        for action,args in [('codex_start',{'project':'unknown','task':'test'}),('codex_start',{'project':'robot','task':'x'*4001}),
            ('codex_start',{'project':'robot','task':''}),('codex_status',{'task_id':'--last'}),('codex_cancel',{'task_id':'../x'})]:
            with self.assertRaises(ValueError):validate_action(self.config,action,args)
        executor=NativeExecutor(dataclasses.replace(self.config,dry_run=True))
        _,data=await executor.execute('codex_start',{'project':'robot','task':'test'})
        self.assertTrue(data['simulated']);self.assertIsNone(executor.codex_tasks.worker)

if __name__=='__main__': unittest.main()
