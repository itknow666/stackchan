import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import wave
import zlib

spec=importlib.util.spec_from_file_location('sd_pack',Path(__file__).parents[1]/'sd-voice-pack.py')
pack=importlib.util.module_from_spec(spec);spec.loader.exec_module(pack)

class SdPack(unittest.TestCase):
    def test_rejects_unsafe_ids_duplicates_missing_and_bad_headpet(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'a.wav';source.write_bytes(b'x')
            for ids,pid,headpet in [(['../x'],'pack',[]),(['x'],'../pack',[]),(['x'],'pack',['x']*10)]:
                with self.assertRaises(ValueError): pack.validate_options([source],ids,pid,headpet)
            with self.assertRaises(ValueError): pack.validate_options([source,source],['x','x'],'pack',[])
            with self.assertRaises(ValueError): pack.validate_options([Path(folder)/'missing'],['x'],'pack',[])

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg not installed')
    def test_real_conversion_crc_manifest_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'sample.wav';output=root/'sd'
            with wave.open(str(source),'wb') as audio:
                audio.setparams((1,2,24000,0,'NONE','not compressed'));audio.writeframes(bytes(4800))
            manifest=pack.build([source],output,['01_hello'],'test-pack')
            self.assertEqual(json.loads((output/'manifest.json').read_text()),manifest)
            clip=manifest['clips'][0];data=(output/clip['file']).read_bytes()
            self.assertEqual(clip['crc32'],zlib.crc32(data));self.assertEqual(clip['sha256'],hashlib.sha256(data).hexdigest())
            self.assertEqual(clip['bytes'],len(data));self.assertTrue(data.startswith(b'OggS'))
            with self.assertRaises(ValueError): pack.build([source],output,['01_hello'],'test-pack')
            self.assertEqual((output/clip['file']).read_bytes(),data)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg not installed')
    def test_oversized_duration_leaves_no_partial_pack(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'long.pcm';source.write_bytes(bytes(48000*16))
            output=Path(folder)/'out'
            with self.assertRaises(ValueError): pack.build([source],output,['long'],'p')
            self.assertFalse(output.exists())

if __name__=='__main__': unittest.main()
