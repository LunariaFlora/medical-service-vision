"""USB camera QR decoding and continuous TCP output; standalone host program."""
import zxingcpp
QR_PAYLOADS = ('11', '13', '31', '33')

def parse_qr(payload):
    record = {'kind': 'QR', 'payload': payload, 'valid': payload in QR_PAYLOADS}
    if record['valid']:
        bed, box = (int(payload[0]), int(payload[1]))
        record.update({'first_bed': bed, 'first_box': box, 'next_bed': 4 - bed, 'next_box': 4 - box})
    else:
        record['reason'] = 'unexpected_qr_payload'
    return record

def decode(gray, code_kind='BOTH'):
    formats = {'BOTH': (zxingcpp.BarcodeFormat.QRCode, zxingcpp.BarcodeFormat.Code128), 'QR': (zxingcpp.BarcodeFormat.QRCode,), 'BAR': (zxingcpp.BarcodeFormat.Code128,)}[code_kind]
    records = []
    for code in zxingcpp.read_barcodes(gray, formats=formats):
        points = [code.position.top_left, code.position.top_right, code.position.bottom_right, code.position.bottom_left]
        records.append({'type': 'QR' if code.format == zxingcpp.BarcodeFormat.QRCode else 'CODE128', 'payload': code.text, 'corners': [[p.x, p.y] for p in points]})
    return records
'持续读取USB二维码：当前帧识别到就发送，不等待回复、不保留旧结果。'
import argparse
from pathlib import Path
import socket
import sys
import time
import cv2

def encode_qr(payload):
    if not parse_qr(payload)['valid']:
        raise ValueError('Invalid QR payload')
    return (payload[0] + ' ' + payload[1] + '\n').encode('ascii')

class TCPOutput:
    """保持连接；故障帧丢弃，下一次连接只发送当时新识别的结果。"""

    def __init__(self, host, port):
        self.address = (host, port)
        self.sock = None
        self.retry_at = 0

    def close(self):
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def send(self, payload):
        packet = encode_qr(payload)
        if self.sock is None and time.monotonic() < self.retry_at:
            return False
        try:
            if self.sock is None:
                self.sock = socket.create_connection(self.address, timeout=0.2)
                self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.sock.sendall(packet)
            return True
        except OSError as exc:
            self.close()
            self.retry_at = time.monotonic() + 1
            print('TCP_UNAVAILABLE', str(exc), flush=True)
            return False

def send_frame(records, output):
    seen = set()
    for record in records:
        payload = record['payload']
        if record['type'] == 'QR' and parse_qr(payload)['valid'] and (payload not in seen):
            seen.add(payload)
            if output.send(payload):
                print('QR', payload, flush=True)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', required=True, type=int)
    parser.add_argument('--camera', required=True, help='Linux /dev/... path or Windows index')
    parser.add_argument('--width', type=int, default=1920)
    parser.add_argument('--height', type=int, default=1080)
    parser.add_argument('--fps', type=int, default=30)
    parser.add_argument('--fourcc', choices=['MJPG', 'YUYV'], default='MJPG')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or min(args.width, args.height, args.fps) <= 0:
        parser.error('Invalid port or camera mode')
    device = int(args.camera) if args.camera.isdecimal() else args.camera
    if not isinstance(device, int) and (not device.startswith('/dev/')):
        parser.error('camera must be an index or /dev/ device path')
    backend = cv2.CAP_V4L2 if sys.platform.startswith('linux') else cv2.CAP_DSHOW
    camera = cv2.VideoCapture(device, backend)
    output = TCPOutput(args.host, args.port)
    try:
        if not camera.isOpened():
            raise OSError('Cannot open USB camera: %s' % device)
        camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*args.fourcc))
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
        camera.set(cv2.CAP_PROP_FPS, args.fps)
        print('READY continuous QR -> TCP | no ACK | Ctrl+C stops', flush=True)
        print('CAMERA actual=%dx%d' % (camera.get(cv2.CAP_PROP_FRAME_WIDTH), camera.get(cv2.CAP_PROP_FRAME_HEIGHT)), flush=True)
        while True:
            ok, frame = camera.read()
            if not ok:
                raise OSError('USB camera returned no frame')
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
            send_frame(decode(gray, 'QR'), output)
    except KeyboardInterrupt:
        print('STOPPED', flush=True)
    except OSError as exc:
        print('CAMERA_ERROR', str(exc), flush=True)
        return 1
    finally:
        camera.release()
        output.close()
if __name__ == '__main__':
    raise SystemExit(main())
