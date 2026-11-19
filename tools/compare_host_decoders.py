"""Offline, same-pixel ZXing-C++ / desktop ZBar comparison; never opens a camera.

Optional deps beyond requirements.txt: pip install pyzbar==0.1.9
Images/results belong in ignored directories. ZBar here is NOT the board firmware.
"""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import statistics
import time

ROOT = Path(__file__).resolve().parents[1]
from PIL import Image, ImageFilter, ImageOps
import zxingcpp
from pyzbar.pyzbar import decode, ZBarSymbol


def sha(data):
    return hashlib.sha256(data).hexdigest()


def zxing(image):
    return [{'payload': x.text, 'format': str(x.format)} for x in
            zxingcpp.read_barcodes(image, formats=(zxingcpp.BarcodeFormat.QRCode,
                                                  zxingcpp.BarcodeFormat.Code128))]


def zbar(image):
    return [{'payload': x.data.decode('utf-8', errors='replace'), 'format': x.type}
            for x in decode(image, symbols=[ZBarSymbol.QRCODE, ZBarSymbol.CODE128])]


def compare(image, repeats):
    result = {}
    pixels = sha(image.tobytes())
    for name, decoder in [('zxing_cpp', zxing), ('desktop_zbar', zbar)]:
        timings, observations = [], []
        for _ in range(repeats):
            start = time.perf_counter()
            try:
                observation = {'records': decoder(image), 'error': None}
            except Exception as exc:
                observation = {'records': [], 'error': '%s: %s' % (type(exc).__name__, exc)}
            timings.append((time.perf_counter() - start) * 1000)
            observations.append(observation)
        assert sha(image.tobytes()) == pixels, 'Decoder modified input'
        result[name] = {'observations': observations,
                        'median_ms': round(statistics.median(timings), 3)}
    return result


def variants(image, roi):
    yield 'original_rgb', image.convert('RGB')
    gray = image.convert('L')
    regions = [('full', gray)]
    if roi:
        x0, y0, x1, y1 = roi
        if not (0 <= x0 < x1 <= image.width and 0 <= y0 < y1 <= image.height):
            raise ValueError('ROI outside image')
        regions.append(('manual_roi', gray.crop(roi)))
    for region, frame in regions:
        for prep, work in [('gray', frame), ('autocontrast', ImageOps.autocontrast(frame)),
                           ('unsharp', frame.filter(ImageFilter.UnsharpMask(
                               radius=1, percent=150, threshold=3)))]:
            yield '%s_%s' % (region, prep), work
            for threshold in range(80, 231, 10):
                yield '%s_%s_bin%d' % (region, prep, threshold), work.point(
                    lambda value: 255 if value >= threshold else 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--roi', nargs=4, type=int, help='Optional manually selected x0 y0 x1 y1')
    parser.add_argument('--repeats', type=int, default=3, help='Timing repeats, NOT success trials')
    parser.add_argument('--provenance', default='user image; acquisition not verified')
    parser.add_argument('--minimal', action='store_true',
                        help='Only original RGB, gray and gray BIN160; no parameter sweep')
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    image = Image.open(args.image)
    image.load()
    report = {'input': str(args.image.resolve()), 'sha256': sha(args.image.read_bytes()),
              'size': list(image.size), 'mode': image.mode, 'provenance': args.provenance,
              'roi': args.roi, 'repeats': args.repeats,
              'versions': {p: importlib.metadata.version(p)
                           for p in ('zxing-cpp', 'pyzbar', 'Pillow')},
              'note': 'Desktop ZBar is not OpenART; no same-byte board comparison performed.',
              'variants': []}
    if args.minimal and args.roi:
        parser.error('--minimal does not crop; omit --roi')
    candidates = variants(image, args.roi)
    if args.minimal:
        gray = image.convert('L')
        candidates = [('original_rgb', image.convert('RGB')), ('full_gray', gray),
                      ('full_gray_bin160', gray.point(lambda value: 255 if value >= 160 else 0))]
    report['minimal'] = args.minimal
    for label, frame in candidates:
        results = compare(frame, args.repeats)
        report['variants'].append({'label': label, 'size': list(frame.size), 'mode': frame.mode,
                                  'pixels_sha256': sha(frame.tobytes()), 'results': results})
        if any(o['records'] or o['error'] for r in results.values() for o in r['observations']):
            print(label, {k: v['observations'][0] for k, v in results.items()})
    # Synthetic positive/negative checks validate plumbing only, not real-image quality.
    report['controls'] = {}
    for text, fmt in [('11', zxingcpp.BarcodeFormat.QRCode),
                      ('6946522463487', zxingcpp.BarcodeFormat.Code128)]:
        control = Image.fromarray(zxingcpp.create_barcode(text, fmt).to_image(scale=4))
        report['controls'][text] = compare(control, args.repeats)
    report['controls']['blank'] = compare(Image.new('L', (640, 480), 255), args.repeats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('RESULT', args.output.resolve(), 'variants=', len(report['variants']))


if __name__ == '__main__':
    main()
