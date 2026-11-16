"""Generate a private scene library with an existing local GPT-SoVITS server.

Does not download models or upload recordings. Requires numpy, scipy, soundfile;
quality verification additionally requires a local faster-whisper model.
"""
import argparse
import csv
import hashlib
import html
import io
import json
import math
import os
import re
import time
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('The local synthesis endpoint must not redirect')


def catalog(path):
    rows = []
    groups = {}
    category = None
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('@'):
            category, name, emotion = line[1:].split('|')
            if not re.fullmatch('[a-z_]{1,40}', category) or category in groups:
                raise ValueError('Invalid or duplicate category')
            groups[category] = 0
        else:
            if category is None or not 2 <= len(line) <= 120:
                raise ValueError('Invalid scene text')
            groups[category] += 1
            n = groups[category]
            rows.append(dict(id=f'{category}_{n:02}', category=category, scene=name,
                             emotion=emotion, text=line,
                             trigger=f'播放{name}第{chinese_number(n)}句'))
    if len(groups) != 20 or set(groups.values()) != {10} or len({r['text'] for r in rows}) != 200:
        raise ValueError('Expected twenty categories, ten unique lines each')
    return rows


def chinese_number(n):
    return '一二三四五六七八九十'[n-1] if 1 <= n <= 10 else str(n)


def atomic_json(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def normalize(text):
    return re.sub(r'[^\u4e00-\u9fff]', '', text).replace('啦', '了').replace('嘛', '吗').replace('哦', '').replace('呀', '')


def clean_audio(data):
    import numpy as np
    import soundfile as sf
    audio, sr = sf.read(io.BytesIO(data), dtype='float32', always_2d=True)
    if not 8000 <= sr <= 96000 or not np.isfinite(audio).all():
        raise ValueError('Invalid audio samples')
    audio = audio.mean(axis=1)
    active = np.flatnonzero(np.abs(audio) > .005)
    if not len(active):
        raise ValueError('Silent recording')
    pad = int(sr * .15)
    audio = audio[max(0, active[0]-pad):min(len(audio), active[-1]+pad+1)]
    seconds = len(audio)/sr
    if not .4 <= seconds <= 14.5:
        raise ValueError('Duration outside safe playback bounds')
    audio *= .80 / float(np.abs(audio).max())
    return audio, sr


def export(output, rows, records):
    good = [dict(row, **records[row['id']]) for row in rows if row['id'] in records and records[row['id']]['passed']]
    atomic_json(output/'catalog.json', good)
    rules = [dict(id=r['id'], file=r['id']+'.pcm', reply=r['text'], triggers=[r['trigger']]) for r in good]
    # No pack_id: this library must not pretend to be the device's current SD pack.
    atomic_json(output/'manifest.json', dict(enabled=True, name='达妮娅场景语音二百句',
        sample_rate=24000, channels=1, encoding='pcm_s16le',
        aliases=['达妮娅','达尼娅','达尼亚','达妮亚'], rules=rules))
    with (output/'台词清单.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['id','scene','text','trigger','duration','asr_score'])
        writer.writeheader()
        writer.writerows({k:r[k] for k in writer.fieldnames} for r in good)
    cards = ''.join('<article data-category="'+html.escape(r['scene'])+'"><h2>'+html.escape(r['scene'])+
        '</h2><p>'+html.escape(r['text'])+'</p><audio controls preload="none" src="wav/'+r['id']+
        '.wav"></audio><small>'+html.escape(r['trigger'])+'</small></article>' for r in good)
    (output/'试听.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<title>达妮娅场景语音</title><style>body{background:#191923;color:#eee;font:16px system-ui;'
        'max-width:1100px;margin:32px auto;padding:16px}main{display:grid;grid-template-columns:repeat('
        'auto-fit,minmax(280px,1fr));gap:16px}article{background:#303044;padding:18px;border-radius:14px}'
        'audio{width:100%}small{display:block;margin-top:8px;color:#cbbdde}input{padding:12px;width:80%}'
        '</style><h1>达妮娅场景语音 · '+str(len(good))+' 句</h1><p>AI 合成语音，非官方录音。'
        '仅通过自动检查，具体听感仍可在此逐条试听。</p><input placeholder="按场景或台词筛选" '
        'oninput="document.querySelectorAll(\'article\').forEach(a=>a.hidden=!a.textContent.includes(this.value))">'
        '<main>'+cards+'</main></html>',encoding='utf-8')
    return len(good)


def generate(args):
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    from faster_whisper import WhisperModel
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    rows = catalog(args.catalog)
    output = args.output.resolve()
    if args.output.is_symlink() or not args.reference.is_file() or not args.asr_model.is_dir():
        raise ValueError('Use regular private output and existing reference/model paths')
    output.mkdir(parents=True, exist_ok=True)
    for name in ('wav','candidates'):
        (output/name).mkdir(exist_ok=True)
    # The local model loader must never silently fetch a different model.
    if args.device == 'cuda' and os.name == 'nt':
        import torch
        dll = str(Path(torch.__file__).parent/'lib')
        dll_handle = os.add_dll_directory(dll)
        os.environ['PATH'] = dll+os.pathsep+os.environ['PATH']
    model = WhisperModel(str(args.asr_model), device=args.device,
                         compute_type='float16' if args.device == 'cuda' else 'int8', cpu_threads=8)
    prompt = args.prompt_text_file.read_text(encoding='utf-8').strip()
    signature = hashlib.sha256((args.catalog.read_text(encoding='utf-8')+str(args.reference.resolve())+
        hashlib.sha256(args.reference.read_bytes()).hexdigest()+prompt+str(args.minimum_score)).encode()).hexdigest()
    report = output/'quality.json'
    if report.exists():
        previous = json.loads(report.read_text(encoding='utf-8'))
        if previous['signature'] != signature:
            raise ValueError('Output belongs to different inputs; use another directory')
        records = previous['records']
    else:
        records = {}
    for row in rows:
        key = row['id']
        if key in records and records[key]['passed']:
            old = records[key]
            if all((output/file).is_file() and hashlib.sha256((output/file).read_bytes()).hexdigest() == digest
                   for file,digest in old['hashes'].items()):
                continue
            raise ValueError('Previously generated asset changed: '+key)
        attempts = []
        for seed in args.seeds:
            try:
                text_hash = hashlib.sha256(row['text'].encode()).hexdigest()[:12]
                candidate = output/'candidates'/f'{key}_{text_hash}_{seed}.wav'
                if candidate.exists():
                    data = candidate.read_bytes()
                else:
                    payload = dict(text=row['text'], text_lang='all_zh', ref_audio_path=str(args.reference.resolve()),
                        prompt_text=prompt, prompt_lang='all_zh', text_split_method='cut0', media_type='wav',
                        streaming_mode=False, batch_size=1, speed_factor=.98, seed=seed,
                        top_k=15, top_p=1, temperature=.7, repetition_penalty=1.35)
                    request = urllib.request.Request(args.endpoint, data=json.dumps(payload,ensure_ascii=False).encode(),
                        headers={'Content-Type':'application/json'})
                    with opener.open(request, timeout=180) as response:
                        data = response.read(16*1024*1024+1)
                    if len(data)>16*1024*1024:
                        raise ValueError('Oversized synthesis result')
                    candidate.write_bytes(data)
                audio, sr = clean_audio(data)
                divisor = math.gcd(sr,16000)
                signal = resample_poly(audio,16000//divisor,sr//divisor)
                segments,_ = model.transcribe(signal, language='zh', beam_size=5, condition_on_previous_text=False)
                recognized = ''.join(s.text for s in segments)
                score = SequenceMatcher(None, normalize(row['text']), normalize(recognized)).ratio()
                attempts.append(dict(seed=seed,asr_score=round(score,4),recognized=recognized))
                print(json.dumps(dict(id=key,**attempts[-1]),ensure_ascii=False),flush=True)
                if score >= args.minimum_score:
                    wav = output/'wav'/(key+'.wav')
                    sf.write(wav,audio,sr,subtype='PCM_16')
                    divisor = math.gcd(sr,24000)
                    pcm = np.clip(resample_poly(audio,24000//divisor,sr//divisor),-.999,.999)
                    pcm = (pcm*32767).round().astype('<i2').tobytes()
                    (output/(key+'.pcm')).write_bytes(pcm)
                    records[key] = dict(passed=True,duration=round(len(pcm)/48000,3),
                        sample_rate=sr,asr_score=round(score,4),recognized=recognized,seed=seed,attempts=attempts,
                        hashes={str(wav.relative_to(output)):hashlib.sha256(wav.read_bytes()).hexdigest(),
                                key+'.pcm':hashlib.sha256(pcm).hexdigest()})
                    break
            except Exception as exc:
                attempts.append(dict(seed=seed,error=type(exc).__name__))
                print(json.dumps(dict(id=key,error=type(exc).__name__),ensure_ascii=False),flush=True)
        else:
            records[key]=dict(passed=False,attempts=attempts)
        atomic_json(report,dict(version=1,signature=signature,minimum_score=args.minimum_score,
            updated_at=int(time.time()),records=records))
        export(output,rows,records)
    count=export(output,rows,records)
    print(json.dumps(dict(passed=count,total=len(rows)),ensure_ascii=False),flush=True)
    return 0 if count == len(rows) else 2


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--reference',type=Path,required=True)
    parser.add_argument('--prompt-text-file',type=Path,required=True)
    parser.add_argument('--asr-model',type=Path,required=True)
    parser.add_argument('--endpoint',default='http://127.0.0.1:9880/tts')
    parser.add_argument('--device',choices=['cuda','cpu'],default='cuda')
    parser.add_argument('--minimum-score',type=float,default=.93)
    parser.add_argument('--seeds',type=int,nargs='+',default=[42,2026,7])
    args=parser.parse_args()
    from urllib.parse import urlsplit
    url=urlsplit(args.endpoint)
    if url.scheme != 'http' or url.hostname not in ('127.0.0.1','localhost') or url.username or url.password or url.path != '/tts' or url.query or url.fragment:
        parser.error('Only an existing local GPT-SoVITS /tts endpoint is supported')
    if not .5 <= args.minimum_score <= 1 or not 1 <= len(args.seeds) <= 12:
        parser.error('Invalid verification threshold or seed count')
    raise SystemExit(generate(args))


if __name__=='__main__':
    main()
