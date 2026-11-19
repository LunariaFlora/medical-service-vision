"""Build a reproducible replay corpus from scan.py results, without inventing ground truth."""
import argparse
import hashlib
import html
import json
from pathlib import Path

from scan import cv2, np, Image


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--video', required=True)
    p.add_argument('--scan-dir', required=True)
    p.add_argument('--out', required=True)
    args = p.parse_args()
    scan = Path(args.scan_dir)
    summary = json.loads((scan/'summary.json').read_text(encoding='utf8'))
    rows = [json.loads(line) for line in (scan/'frames.jsonl').read_text(encoding='utf8').splitlines()]
    if not rows or summary['state'] != 'complete':
        raise ValueError('Complete baseline scan required')
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if (out/'manifest.json').exists():
        raise ValueError('Dataset exists; choose a new output')
    # Cover time uniformly, then preserve first hits per value and hit/miss in each 10s window.
    chosen = {}
    def add(row, reason):
        chosen.setdefault(row['frame'], {'row':row, 'reasons':[]})['reasons'].append(reason)
    for t in np.arange(0, rows[-1]['time_ms']+1, 2000):
        add(min(rows, key=lambda r:abs(r['time_ms']-t)), 'uniform_2s')
    seen = set()
    windows = set()
    for row in rows:
        for code in row['records']:
            key = (code['type'],code['payload'])
            if key not in seen:
                add(row,'first_decode_value'); seen.add(key)
        key = (int(row['time_ms']//10000),bool(row['records']))
        if key not in windows:
            add(row,'first_hit_or_miss_in_10s'); windows.add(key)
    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened(): raise ValueError('Cannot open video')
    samples = []
    hashes = {}
    try:
        for index, selected in sorted(chosen.items()):
            cap.set(cv2.CAP_PROP_POS_FRAMES,index)
            ok, frame = cap.read()
            if not ok: raise ValueError('Missing selected frame %d'%index)
            if summary['roi']:
                x,y,w,h = summary['roi']; frame = frame[y:y+h,x:x+w]
            gray = cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
            digest = hashlib.sha256(gray.tobytes()).hexdigest()
            name = 'frame_%06d.png'%index
            Image.fromarray(gray).save(out/name)
            row = selected['row']
            samples.append({'file':name,'frame':index,'time_ms':row['time_ms'],
                            'selection':selected['reasons'],'sha256_pixels':digest,
                            'exact_duplicate_of':hashes.get(digest),'shape':list(gray.shape),
                            'ground_truth':None,'label_status':'unverified',
                            'observed_records':row['records'],
                            'scene_tags':[], 'note':'Screenshot crop; not a raw sensor frame'})
            hashes.setdefault(digest,name)
    finally:
        cap.release()
    digest = hashlib.sha256()
    with open(args.video,'rb') as source:
        for chunk in iter(lambda:source.read(1024*1024), b''):
            digest.update(chunk)
    video_hash = digest.hexdigest()
    manifest = {'source':str(Path(args.video).resolve()),'source_sha256':video_hash,
                'roi':summary['roi'],'baseline':summary,'samples':samples,
                'purpose':'Regression/exploration corpus; not an independent benchmark or training/test split',
                'ground_truth_policy':'Decoder output is an observation, never an automatically accepted true label'}
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
    body = '<!doctype html><meta charset="utf-8"><title>条码录屏样本集</title><style>body{font:17px sans-serif;margin:24px;background:#f5f6f8}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:16px}article{background:white;padding:16px}img{width:100%}p{overflow-wrap:anywhere}</style>'
    body += '<h1>条码录屏样本集</h1><p>全片处理%d帧，其中%d帧有解码结果。</p>'%(summary['frames_processed'],summary['frames_with_decode'])
    body += '<p>样本共%d帧。默认每2秒取样，补充命中/未命中代表帧；不是独立成功率。点击查看原尺寸。识别文字未经真值核对。</p><main>'%len(samples)
    for s in samples:
        values = [(r['type'],r['payload']) for r in s['observed_records']]
        body += '<article><b>%.2f秒 / 帧%d</b><p>%s</p><a href="%s"><img loading="lazy" src="%s"></a></article>'%(s['time_ms']/1000,s['frame'],html.escape(str(values) if values else '未解出'),s['file'],s['file'])
    (out/'index.html').write_text(body+'</main>',encoding='utf8')
    print('DATASET',out,'samples',len(samples),'unique_pixels',len(hashes))


if __name__ == '__main__':
    main()
