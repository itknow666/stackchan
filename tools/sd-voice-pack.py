"""Build a private, bounded StackChan SD voice pack. Never uploads recordings."""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import zlib
from pathlib import Path

ID=re.compile(r'[A-Za-z0-9_-]{1,64}\Z')
MAX_BYTES=256*1024

def validate_options(inputs,ids,pack_id,headpet):
    if not isinstance(pack_id,str) or not ID.fullmatch(pack_id): raise ValueError('Use an ASCII pack ID of one to sixty-four letters, digits, hyphens or underscores')
    if not 1<=len(inputs)<=64 or len(inputs)!=len(ids): raise ValueError('Supply one to sixty-four files and one ID per file')
    if any(not isinstance(i,str) or not ID.fullmatch(i) for i in ids) or len(set(ids))!=len(ids): raise ValueError('Clip IDs must be safe and unique')
    if headpet and (len(headpet)!=10 or len(set(headpet))!=10 or any(i not in ids for i in headpet)): raise ValueError('Head-pet order must contain exactly ten unique clip IDs from this pack')
    for path in inputs:
        if path.is_symlink() or not path.is_file(): raise ValueError('Every input must be a regular recording, not a symlink')

def probe(path,raw=False):
    options=['-f','s16le','-ar','24000','-ch_layout','mono'] if raw else []
    result=subprocess.run(['ffprobe','-v','error',*options,'-select_streams','a:0',
        '-show_entries','stream=codec_name,channels:format=duration:packet=duration_time',
        '-show_packets','-of','json',str(path)],check=True,capture_output=True,text=True,encoding='utf-8',timeout=30)
    return json.loads(result.stdout)

def build(inputs,output,ids,pack_id,headpet=()):
    inputs=[Path(p) for p in inputs];output=Path(output)
    validate_options(inputs,ids,pack_id,headpet)
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'): raise ValueError('Install FFmpeg and ffprobe')
    if output.is_symlink() or (output.exists() and (not output.is_dir() or any(output.iterdir()))): raise ValueError('Output must be a new or empty directory; existing resources are never overwritten')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.dania-pack-',dir=output.parent) as temporary:
        stage=Path(temporary)/'pack';stage.mkdir()
        clips=[]
        for source,clip_id in zip(inputs,ids):
            raw=source.suffix.lower()=='.pcm'
            if raw and (source.stat().st_size==0 or source.stat().st_size%2): raise ValueError('PCM inputs must be nonempty 24 kHz mono PCM16')
            source_info=probe(source,raw)
            duration=float(source_info.get('format',{}).get('duration',0))
            if not 0<duration<=15: raise ValueError('Recordings must be at most fifteen seconds')
            target=stage/(clip_id+'.ogg')
            options=['-f','s16le','-ar','24000','-ac','1'] if raw else []
            subprocess.run(['ffmpeg','-v','error','-nostdin','-n',*options,'-i',str(source),
                '-vn','-ar','24000','-ac','1','-c:a','libopus','-b:a','24k',
                '-frame_duration','60','-application','voip',str(target)],check=True,timeout=60)
            encoded=probe(target)
            streams=encoded.get('streams',[])
            # The embedded demuxer decodes complete sixty-ms packets, including
            # the codec tail. Bound that real playback duration, not just tags.
            if (len(streams)!=1 or streams[0].get('codec_name')!='opus' or streams[0].get('channels')!=1 or
                not 1<=len(encoded.get('packets',[]))<=250): raise ValueError('Encoded clip exceeds fifteen seconds including its Opus tail; shorten it slightly')
            data=target.read_bytes()
            if not 0<len(data)<=MAX_BYTES: raise ValueError('Encoded clips must be nonempty and at most 256 KiB')
            clips.append({'id':clip_id,'file':target.name,'bytes':len(data),
                          'sha256':hashlib.sha256(data).hexdigest(),'crc32':zlib.crc32(data)})
        manifest={'version':1,'pack_id':pack_id,'clips':clips,'headpet':list(headpet)}
        text=json.dumps(manifest,ensure_ascii=False,indent=2)
        if len(text.encode())>32768: raise ValueError('Manifest exceeds 32 KiB')
        (stage/'manifest.json').write_text(text,encoding='utf-8')
        if output.exists(): output.rmdir() # verified empty; a racing file makes this fail safely
        stage.rename(output)
    return manifest

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path,nargs='+')
    parser.add_argument('--output',type=Path,required=True,help='Private staging directory; copy its contents to SD /dania/voice-pack')
    parser.add_argument('--pack-id',required=True)
    parser.add_argument('--ids',nargs='+',help='Default: safe input filename stems. Match cloud rule IDs for scene recordings')
    parser.add_argument('--headpet',nargs='*',default=[],help='Ten clip IDs in head-pet playback order')
    args=parser.parse_args()
    try: manifest=build(args.input,args.output,args.ids or [p.stem for p in args.input],args.pack_id,args.headpet)
    except (ValueError,OSError,subprocess.SubprocessError) as error: parser.exit(1,str(error)+'\n')
    print('Built private SD pack:',len(manifest['clips']),'clips. Copy to /dania/voice-pack; restart to reload.')

if __name__=='__main__': main()
