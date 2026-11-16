import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('scene_voices', ROOT/'tools/generate-scene-voices.py')
scene = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scene)


class SceneVoicesTest(unittest.TestCase):
    def test_catalog_has_200_unique_safe_calls_and_work_scenarios(self):
        rows = scene.catalog(ROOT/'examples/scene-lines.zh.txt')
        self.assertEqual(len(rows), 200)
        self.assertEqual(len({r['id'] for r in rows}), 200)
        self.assertEqual(len({r['trigger'] for r in rows}), 200)
        self.assertTrue({'job_done','job_failed','job_cancelled','pc_work','checks'} <= {r['category'] for r in rows})

    def test_incomplete_catalog_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'bad.txt'
            path.write_text('@job_done|完成|HAPPY\n一条台词。\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                scene.catalog(path)

    def test_failed_recordings_never_export_as_callable(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            rows = [dict(id='job_done_01',category='job_done',scene='完成',emotion='HAPPY',text='完成啦。',trigger='播放完成第一句')]
            scene.export(out, rows, {'job_done_01':dict(passed=False,attempts=[])})
            self.assertEqual(json.loads((out/'manifest.json').read_text(encoding='utf-8'))['rules'], [])
            self.assertEqual(json.loads((out/'catalog.json').read_text(encoding='utf-8')), [])

    def test_silent_and_overlong_audio_rejected_and_valid_audio_bounded(self):
        try:
            import numpy as np
            import soundfile as sf
        except ImportError:
            self.skipTest('Optional voice-generation dependencies are not installed')
        for seconds, amplitude in [(1,0),(16,.2)]:
            data = io.BytesIO()
            samples = np.ones(int(seconds*24000),dtype='float32')*amplitude
            sf.write(data,samples,24000,format='WAV',subtype='PCM_16')
            with self.assertRaises(ValueError): scene.clean_audio(data.getvalue())
        data=io.BytesIO()
        sf.write(data,np.sin(np.arange(24000)*.1)*.3,24000,format='WAV',subtype='PCM_16')
        audio,sr=scene.clean_audio(data.getvalue())
        self.assertEqual(sr,24000)
        self.assertLessEqual(float(np.abs(audio).max()),.801)


if __name__=='__main__': unittest.main()
