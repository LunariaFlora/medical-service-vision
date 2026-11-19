"""Image/video/USB -> ZXing QR + Code128 -> JSONL and HTML. No ROS/motor actions."""
import argparse
import html
import json
from pathlib import Path
import threading
import time

import cv2
import numpy as np
from PIL import Image
import serial
import zxingcpp
from wire import receive


def decode(gray, code_kind="BOTH"):
    formats = {"BOTH": (zxingcpp.BarcodeFormat.QRCode, zxingcpp.BarcodeFormat.Code128),
               "QR": (zxingcpp.BarcodeFormat.QRCode,),
               "BAR": (zxingcpp.BarcodeFormat.Code128,)}[code_kind]
    records = []
    for code in zxingcpp.read_barcodes(gray, formats=formats):
        points = [code.position.top_left, code.position.top_right,
                  code.position.bottom_right, code.position.bottom_left]
        records.append({'type': 'QR' if code.format == zxingcpp.BarcodeFormat.QRCode else 'CODE128',
                        'payload': code.text, 'corners': [[p.x, p.y] for p in points]})
    return records



def barcode_like(gray):
    """保守的条纹外观提示，不解码、不声称检测到真实条码。双方向兼容横竖摆放。"""
    gx = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
    gy = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
    for primary, cross, kernel in ((gx,gy,(31,9)), (gy,gx,(9,31))):
        edges = ((primary > 40) & (primary > cross*1.5)).astype(np.uint8)
        density = cv2.boxFilter(edges.astype(np.float32), -1, kernel)
        mask = (density > 0.22).astype(np.uint8)
        count, labels, stats, centers = cv2.connectedComponentsWithStats(mask)
        if any(area >= 250 and max(w,h) >= 40 for x,y,w,h,area in stats[1:]):
            return True
    return False


def images(args, stop=None):
    if getattr(args, "usb_results", None):
        from result_transport import frames
        yield from frames(args, stop)
        return
    if getattr(args, "openart", None):
        from openart_transport import frames
        yield from frames(args, stop)
        return
    if args.usb:
        port = serial.Serial(args.usb, 115200, timeout=0.2, write_timeout=2)
        try:
            port.dtr = True
            port.reset_input_buffer()
            start = time.monotonic()
            index = 0
            while args.frames == 0 or index < args.frames:
                if stop is not None and stop.is_set():
                    return
                if port.write(b'SNAP') != 4:
                    raise OSError('Incomplete SNAP request')
                args.capture_timing = {}
                sequence, width, height, data = receive(port, timings=args.capture_timing)  # finish in-flight frame before closing USB
                yield index, (time.monotonic() - start) * 1000, np.frombuffer(data, np.uint8).reshape(height, width).copy()
                index += 1
        finally:
            port.close()
        return
    path = Path(args.input)
    if path.suffix.lower() in ('.bmp', '.png', '.jpg', '.jpeg', '.tif', '.tiff'):
        yield 0, 0.0, np.array(Image.open(path).convert('L'))
        return
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError('Cannot open video: ' + str(path))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            raise ValueError('Video has no usable frame rate')
        index = 0
        while True:
            if stop is not None and stop.is_set():
                return
            ok, frame = cap.read()
            if not ok:
                break
            if index % args.every == 0:
                yield index, index * 1000 / fps, cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            index += 1
    finally:
        cap.release()


def run(args):
    if not getattr(args, 'preview', False):
        return collect(args)
    from preview import LivePreview
    display = LivePreview(args.out, mission=getattr(args, 'mission_controller', None), result_mode=bool(getattr(args,'usb_results',None)))
    args.snapshot_request = display.snapshot_request
    if getattr(args, "snapshot_first", False): display.request_snapshot()
    result = {}

    def worker():
        try:
            result['summary'] = collect(args, display)
        except Exception as exc:
            result['error'] = exc
            if not display.finished.is_set():
                display.finish('error: %s: %s' % (type(exc).__name__, exc))

    thread = threading.Thread(target=worker, name='capture-decoder')
    thread.start()
    try:
        display.mainloop()
    finally:
        display.stop.set()
        thread.join()
    if 'error' in result:
        raise result['error']
    return result['summary']


def collect(args, display=None):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if (out / 'frames.jsonl').exists():
        raise ValueError('Output already contains a run; choose a new --out directory')
    total = hits = 0
    unique = set()
    cards = []
    previous = None
    timing_sums = {}
    started = time.monotonic()
    stopped = 'complete'
    mission = getattr(args, 'mission_controller', None)
    try:
        with (out / 'frames.jsonl').open('w', encoding='utf8') as log:
            stop = display.stop if display else getattr(args, 'stop_event', None)
            source = iter(images(args, stop))
            while True:
                # Capture the stage BEFORE requesting a frame; never assign an old frame to a new bed.
                token = mission.capture_token() if mission else None
                cycle_start = time.perf_counter()
                try:
                    index, timestamp, gray = next(source)
                except StopIteration:
                    break
                capture_ms = (time.perf_counter() - cycle_start) * 1000
                if stop is not None and stop.is_set():
                    break
                if args.roi and gray is not None:
                    x, y, w, h = args.roi
                    if x < 0 or y < 0 or w <= 0 or h <= 0 or x+w > gray.shape[1] or y+h > gray.shape[0]:
                        raise ValueError('ROI outside frame; use recording pixel coordinates')
                    gray = gray[y:y+h, x:x+w].copy()
                t0 = time.perf_counter()
                records = (args.board_result["records"] if getattr(args,"usb_results",None) else decode(gray, getattr(args, "code", "BOTH")))
                decode_ms = round((time.perf_counter()-t0)*1000, 2)
                # Persist raw outputs before candidate selection, including suspicious strings and multi-code frames.
                with (out/'observations.jsonl').open('a', encoding='utf-8') as raw_log:
                    raw_log.write(json.dumps({'frame':index, 'time_ms':timestamp, 'records':records}, ensure_ascii=False)+'\n')
                mission_start = time.perf_counter()
                appearance = (args.board_result['barcode_like'] if getattr(args,'usb_results',None) else
                              (bool(any(r['type']=='CODE128' for r in records)) or barcode_like(gray))) if mission else None
                if getattr(args,'usb_results',None) and args.board_result['errors']:
                    appearance = None
                accepted = mission.observe(records, token, now=timestamp/1000, barcode_like=appearance) if mission else False
                mission_ms = (time.perf_counter()-mission_start)*1000
                item = {'frame': index, 'time_ms': round(timestamp, 2), 'size': [gray.shape[1], gray.shape[0]] if gray is not None else None,
                        'records': records, 'decode_ms': decode_ms,
                        'confirmed_mission': False,
                        'code_profile': getattr(args, 'code', 'BOTH'),
                        'capture_transport': getattr(args, 'capture_transport', 'rcf1_raw_gray' if args.usb else 'file'),
                        'setup': getattr(args, 'setup', '')}
                if getattr(args,'usb_results',None):
                    item['board_result'] = args.board_result
                if mission:
                    item.update(mission=mission.snapshot(), mission_accepted=accepted, barcode_like=appearance)
                if getattr(args,'usb_results',None) and gray is not None:
                    item['host_comparison'] = decode(gray, 'BOTH')
                    name = 'original_%08d' % index
                    Image.fromarray(gray).save(out/(name+'.png'))
                    item['original_file'] = str(out/(name+'.png'))
                    (out/(name+'.json')).write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding='utf-8')
                if display:
                    display.publish(gray, item)
                total += 1
                hits += bool(records)
                signature = tuple(sorted((r['type'], r['payload']) for r in records))
                unique.update(signature)
                if signature != previous or total % 30 == 0:
                    print(('READ' if records else 'NO_CODE'), 'frame=', index,
                          'time_ms=', round(timestamp), 'payloads=', signature, flush=True)
                if gray is not None and (signature != previous or total == 1) and len(cards) < 100:
                    name = 'frame_%08d.png' % index
                    # Saved evidence is original cropped gray, without annotations.
                    Image.fromarray(gray).save(out / name)
                    cards.append((name, index, timestamp, signature))
                previous = signature
                timings = dict(getattr(args, 'capture_timing', {}))
                timings.update(capture_ms=capture_ms, decode_ms=decode_ms, mission_ms=mission_ms,
                               host_other_ms=max(0, (time.perf_counter()-cycle_start)*1000-capture_ms-decode_ms-mission_ms))
                item['timing_ms'] = {k: round(v, 3) for k,v in timings.items()}
                for key, value in item['timing_ms'].items():
                    timing_sums[key] = timing_sums.get(key, 0) + value
                log.write(json.dumps(item, ensure_ascii=False) + '\n')
                log.flush()
            if stop is not None and stop.is_set():
                stopped = 'preview_stop'
    except InterruptedError:
        stopped = 'preview_stop'
    except KeyboardInterrupt:
        stopped = 'user_interrupt'
    except Exception as exc:
        stopped = 'error: %s: %s' % (type(exc).__name__, exc)
    finally:
        if 'source' in locals() and hasattr(source, 'close'):
            source.close()
    summary = {'state': stopped, 'source': getattr(args, 'usb_results', None) or getattr(args, 'openart', None) or args.usb or args.input,
               'code_profile': getattr(args, 'code', 'BOTH'), 'setup': getattr(args, 'setup', ''), 'roi': args.roi,
               'frames_processed': total, 'frames_with_decode': hits,
               'unique_payloads': sorted(unique), 'elapsed_s': round(time.monotonic()-started, 2),
               'note': 'Frame counts are not independent success rates. Raw observations, no mission commands.'}
    if total:
        summary['timing_mean_ms'] = {k: round(v/total,3) for k,v in timing_sums.items()}
        summary['timing_note'] = 'capture includes camera/USB wait; header wait is not pure exposure; host_other includes preview queue and PNG saves, excludes asynchronous GUI rendering and JSONL flush.'
    (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf8')
    body = '<!doctype html><meta charset="utf-8"><title>扫码回放结果</title><style>body{font:18px sans-serif;max-width:1000px;margin:32px auto}img{max-width:100%;max-height:480px}article{border-top:1px solid #ccc;padding:20px}pre{white-space:pre-wrap}</style>'
    body += '<h1>扫码回放结果</h1><pre>' + html.escape(json.dumps(summary, ensure_ascii=False, indent=2)) + '</pre>'
    for name, index, stamp, sig in cards:
        body += '<article><p>帧 %d · %.2f秒 · %s</p><img src="%s"></article>' % (index, stamp/1000, html.escape(str(sig) if sig else '未识别'), name)
    (out / 'report.html').write_text(body, encoding='utf8')
    print('REPORT', (out / 'report.html').resolve())
    if display:
        display.finish(stopped)
    if stopped.startswith('error:') or (total == 0 and stopped != 'preview_stop'):
        raise RuntimeError(stopped if stopped.startswith('error:') else 'No frames processed')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--input', help='Image or recording; never opens a webcam')
    source.add_argument('--openart', help='OpenART legacy USB debug; runs capture-only RAM script; close IDE first')
    parser.add_argument('--code', choices=('BOTH','QR','BAR'), default='BOTH')
    parser.add_argument('--setup', default='', help='Board/lens and distance notes saved with frames')
    parser.add_argument('--exposure', type=int, default=250, help='OpenART requested setting, readback unverified')
    source.add_argument('--usb-results', help='H7 native decode results over USB; no live images')
    source.add_argument('--usb', help='OpenMV native USB VCP port; IDE must release it')
    parser.add_argument('--snapshot-first', action='store_true', help='Request one original frame when opening the result window')
    parser.add_argument('--preview', action='store_true', help='Visible live results; requires a desktop')
    parser.add_argument('--mission', action='store_true', help='Enable route and two-bed task state')
    parser.add_argument('--mock-qr', choices=('11', '13', '31', '33'),
                        help='Explicit test preset; omit to wait for a real QR')
    parser.add_argument('--frames', type=int, default=300, help='Maximum USB frames; 0 = until stopped (preview only)')
    parser.add_argument('--every', type=int, default=1, help='Video frame sampling interval')
    parser.add_argument('--roi', nargs=4, type=int, metavar=('X','Y','W','H'))
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    if args.snapshot_first and not (args.usb_results and args.preview):
        parser.error('--snapshot-first requires --usb-results and --preview')
    if args.usb_results and args.roi:
        parser.error('ROI requires image input')
    if args.mock_qr and not args.mission:
        parser.error('--mock-qr requires --mission')
    if args.mission:
        from mission import Mission, LocalInterfaces
        if (Path(args.out) / 'mission_state.json').exists():
            parser.error('Choose a new output directory for a new mission')
        args.mission_controller = Mission(LocalInterfaces(args.out), args.mock_qr)
    if args.exposure <= 0 or args.every < 1 or args.frames < 0 or (args.frames == 0 and not args.preview):
        parser.error('every must be positive; frames=0 requires --preview')
    run(args)


if __name__ == '__main__':
    main()
