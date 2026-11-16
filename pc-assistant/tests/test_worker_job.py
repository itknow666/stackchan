import os
import subprocess
import sys
import unittest


@unittest.skipUnless(os.name == 'nt', 'Windows job objects')
class WorkerJobTests(unittest.TestCase):
    def test_job_close_stops_worker(self):
        from dania_pc.worker_job import WorkerJob
        process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        job=None
        try:
            job=WorkerJob(process.pid)
            job.close()
            process.wait(timeout=5)
            self.assertIsNotNone(process.returncode)
        finally:
            if job:job.close()
            if process.poll() is None:process.kill();process.wait(timeout=5)

if __name__=='__main__':unittest.main()
