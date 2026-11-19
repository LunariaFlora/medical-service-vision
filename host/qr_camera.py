"""第二路USB UVC二维码：独立采集进程，复用ZXing及既有QR规则解析。"""
import json
import multiprocessing
from pathlib import Path
import queue
import sys
import threading
import time

# Same repository-root convention as mission.py, so the shared payload parser is importable
# no matter which directory this module is imported from.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from firmware.qr_scanner import parse_qr


def select_qr(records):
    # 同框多种码不随便取第一项；即使另一项非法也作为歧义保留。
    values = {r['payload'] for r in records if r['type'] == 'QR'}
    if len(values) != 1:
        return None
    value = next(iter(values))
    return value if parse_qr(value)['valid'] else None


def capture(config, output, messages, stop):
    """独立进程防止驱动read卡住整个条码/接口服务；全部解码观察先落盘。"""
    import cv2
    from scan import decode
    import sys
    camera = None
    output = Path(output)
    try:
        backend = cv2.CAP_V4L2 if sys.platform.startswith('linux') else cv2.CAP_DSHOW
        camera = cv2.VideoCapture(config['device'], backend)
        if not camera.isOpened():
            raise OSError('Cannot open QR UVC device')
        if config['fourcc']:
            camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*config['fourcc']))
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, config['width'])
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, config['height'])
        camera.set(cv2.CAP_PROP_FPS, config['fps'])
        actual = {key: camera.get(prop) for key, prop in (
            ('width',cv2.CAP_PROP_FRAME_WIDTH), ('height',cv2.CAP_PROP_FRAME_HEIGHT),
            ('fps',cv2.CAP_PROP_FPS), ('fourcc',cv2.CAP_PROP_FOURCC))}
        (output/'qr_camera_info.json').write_text(json.dumps(
            {'requested':config, 'reported':actual}), encoding='utf-8')
        messages.put({'state':'opened', 'actual':actual})
        index, last_image = 0, 0
        with (output/'qr_observations.jsonl').open('a', encoding='utf-8') as log:
            while not stop.is_set():
                ok, frame = camera.read()
                if not ok:
                    raise OSError('QR camera returned no frame')
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
                records = decode(gray, 'QR')
                row = {'state':'frame', 'frame':index, 'unix_time':time.time(),
                       'size':[gray.shape[1],gray.shape[0]], 'records':records}
                log.write(json.dumps(row, ensure_ascii=False)+'\n'); log.flush()
                if time.monotonic()-last_image >= 0.5 or records:
                    ok, png = cv2.imencode('.png', gray)
                    if ok:
                        tmp = output/'qr_latest.tmp'
                        tmp.write_bytes(png.tobytes()); tmp.replace(output/'qr_latest.png')
                    last_image = time.monotonic()
                messages.put(row)
                index += 1
    except Exception as exc:
        messages.put({'state':'error', 'error':str(exc)})
    finally:
        if camera is not None:
            camera.release()


class QRCamera:
    def __init__(self, config, mission, output, capture_target=capture):
        self.config, self.mission, self.output = config, mission, Path(output)
        self.stop = threading.Event()
        self.capture_target = capture_target
        self.thread = threading.Thread(target=self.run, name='qr-supervisor', daemon=True)

    def status(self, row):
        row = dict(row, updated_unix=time.time())
        tmp = self.output/'qr_status.tmp'
        tmp.write_text(json.dumps(row, ensure_ascii=False), encoding='utf-8')
        tmp.replace(self.output/'qr_status.json')

    def run(self):
        ctx = multiprocessing.get_context('spawn')
        while not self.stop.is_set() and self.mission.snapshot()['plan'] is None:
            messages, stopped = ctx.Queue(maxsize=8), ctx.Event()
            process = ctx.Process(target=self.capture_target, args=(self.config,str(self.output),messages,stopped), daemon=True)
            process.start()
            last = time.monotonic()
            self.status({'state':'opening'})
            try:
                while not self.stop.is_set():
                    try:
                        row = messages.get(timeout=0.2)
                    except queue.Empty:
                        if not process.is_alive() or time.monotonic()-last > 10:
                            self.status({'state':'error','error':'QR capture stopped or timed out'})
                            break
                        continue
                    last = time.monotonic()
                    self.status(row)
                    if row['state'] == 'error':
                        break
                    if row['state'] == 'frame':
                        payload = select_qr(row['records'])
                        if payload is not None and self.mission.set_qr(payload, source='USB_CAMERA'):
                            self.status({'state':'locked', 'payload':payload})
                            return
            finally:
                stopped.set()
                process.join(timeout=1)
                if process.is_alive():
                    process.terminate(); process.join(timeout=2)
                messages.close()
            self.stop.wait(2)

    def start(self):
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=4)
