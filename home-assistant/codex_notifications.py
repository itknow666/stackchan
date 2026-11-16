"""Durable, at-most-once announcements from the trusted local PC status projection."""
import asyncio
import json
import os
import re
import time
from pathlib import Path

STATES = {'completed', 'failed', 'cancelled', 'timed_out', 'interrupted'}


class CompletionNotifier:
    def __init__(self, config):
        if not isinstance(config, dict) or config.keys() - {'enabled','ledger_file','clips','max_age_seconds'}:
            raise ValueError('Invalid Codex notification configuration')
        if type(config.get('enabled',False)) is not bool: raise ValueError('Invalid notification switch')
        self.enabled = config.get('enabled',False)
        self.path = Path(config.get('ledger_file','private/codex-notifications.json'))
        if not self.path.is_absolute(): raise ValueError('Notification ledger path must be absolute')
        self.clips = config.get('clips',{})
        if not isinstance(self.clips,dict) or self.clips.keys() - STATES or any(not isinstance(v,str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}',v) for v in self.clips.values()):
            raise ValueError('Invalid notification clips')
        self.max_age = config.get('max_age_seconds',120)
        if type(self.max_age) is not int or not 10 <= self.max_age <= 600: raise ValueError('Invalid notification age')
        self.ledger = {}
        if self.path.exists():
            if self.path.stat().st_size > 2_000_000: raise ValueError('Notification ledger too large')
            self.ledger = json.loads(self.path.read_text(encoding='utf-8'))
            if (not isinstance(self.ledger,dict) or len(self.ledger)>2048 or
                any(not isinstance(k,str) or not re.fullmatch('[a-f0-9]{32}',k) or not isinstance(v,dict) or
                    type(v.get('at_ms')) is not int or not isinstance(v.get('state'),str) for k,v in self.ledger.items())):
                raise ValueError('Invalid notification ledger')

    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temp=self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.ledger),encoding='utf-8');os.replace(temp,self.path)

    async def poll(self, status, ble_connected, play, now=None):
        if not self.enabled or not ble_connected or status is None: return
        now=int(time.time()*1000) if now is None else now
        try:
            if not status['connected'] or status['dry_run']: return
            tasks=status.get('codex_tasks',[])
            if not isinstance(tasks,list) or len(tasks)>20: return
            for task in tasks:
                if not isinstance(task,dict): continue
                task_id=task.get('task_id');state=task.get('state');finished=task.get('finished_at_ms')
                if not isinstance(task_id,str) or not re.fullmatch('[a-f0-9]{32}',task_id) or state not in self.clips or type(finished)!=int or not 0<=now-finished<=self.max_age*1000: continue
                old=self.ledger.get(task_id)
                if old and old.get('state')!='unavailable': continue
                # Retain bounded tombstones beyond the maximum replay window.
                self.ledger={k:v for k,v in self.ledger.items() if now-v['at_ms']<=86400000}
                if len(self.ledger)>=2048: return
                self.ledger[task_id]={'state':'attempted','at_ms':now}
                self.save()  # Durable BEFORE the physical command. Unknown outcomes never replay.
                try: result=await play(self.clips[state])
                except asyncio.CancelledError: raise
                except Exception: result='unknown'
                self.ledger[task_id]={'state':result if result in {'completed','unavailable','failed','cancelled'} else 'unknown','at_ms':now}
                self.save()
                return
        except (OSError,ValueError,TypeError,KeyError): return
