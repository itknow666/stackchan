"""Convert recordings with FFmpeg; never upload the generated private outputs."""
import argparse
from pathlib import Path
import shutil
import subprocess

parser=argparse.ArgumentParser()
parser.add_argument('mode',choices=('cloud','headpet'))
parser.add_argument('input',type=Path,nargs='+')
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
if not shutil.which('ffmpeg'):raise SystemExit('Install FFmpeg and add it to PATH')
if args.mode=='headpet' and len(args.input)!=10:raise SystemExit('Head-pet mode requires exactly ten input recordings in playback order')
args.output.mkdir(parents=True,exist_ok=True)
for index,source in enumerate(args.input,1):
    if not source.is_file():raise SystemExit('Recording not found: '+str(source))
    name=source.stem+'.pcm' if args.mode=='cloud' else f'dania_cute_{index:02d}.ogg'
    target=args.output/name
    if target.exists():raise SystemExit('Refusing to overwrite: '+str(target))
    encoding=['-c:a','pcm_s16le','-f','s16le'] if args.mode=='cloud' else ['-c:a','libopus','-b:a','24k','-frame_duration','60','-application','voip']
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-n','-i',str(source),
                    '-vn','-ar','24000','-ac','1',*encoding,str(target)],check=True)
    if args.mode=='cloud' and not 0<target.stat().st_size<=24000*2*15:
        target.unlink()
        raise SystemExit('Cloud clips must be nonempty and at most fifteen seconds')
print('Converted',len(args.input),'private clips; update your local manifest before enabling')
