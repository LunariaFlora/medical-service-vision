"""USB摄像头条码识别（主机运行）。依赖：opencv-python-headless、zxing-cpp。

python barcode_usb.py --camera /dev/video0 --host 127.0.0.1 --port 9001
每次识别到的Code128原文通过TCP发送并打印，每码一行；不等ACK。
可导入decode_barcodes(gray)处理图像，或barcode_frames(device)持续取帧解码。
"""
import argparse
from pathlib import Path
import sys
import socket
import time

# 本仓库电脑测试复用已有依赖；复制到其他主机后使用那里安装的依赖。
_local_deps = Path(__file__).resolve().parents[1] / 'build' / 'host_deps'
if _local_deps.is_dir():
    sys.path.insert(0, str(_local_deps))
import cv2
import zxingcpp


def decode_barcodes(gray):
    """返回本帧全部非空Code128原文及坐标；不设六样本白名单/长度限制。"""
    records = []
    for code in zxingcpp.read_barcodes(gray, formats=(zxingcpp.BarcodeFormat.Code128,)):
        if not code.text:
            continue
        points = [code.position.top_left, code.position.top_right,
                  code.position.bottom_right, code.position.bottom_left]
        records.append({'type': 'CODE128', 'payload': code.text,
                        'corners': [[point.x, point.y] for point in points]})
    return records


def barcode_frames(device, width=1920, height=1080, fps=30):
    """逐帧产生结果列表；空帧产生[]，调用方决定是否输出。close()释放相机。"""
    backend = cv2.CAP_V4L2 if sys.platform.startswith('linux') else cv2.CAP_DSHOW
    camera = cv2.VideoCapture(device, backend)
    try:
        if not camera.isOpened():
            raise OSError('Cannot open camera %s; close other camera applications' % device)
        camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        camera.set(cv2.CAP_PROP_FPS, fps)
        print('CAMERA %s actual=%dx%d' % (device,
              camera.get(cv2.CAP_PROP_FRAME_WIDTH), camera.get(cv2.CAP_PROP_FRAME_HEIGHT)),
              file=sys.stderr, flush=True)
        while True:
            ok, frame = camera.read()
            if not ok:
                raise OSError('Camera returned no frame')
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
            yield decode_barcodes(gray)
    finally:
        camera.release()


def encode_records(records):
    """本帧码值原文+CRLF，不跨帧去重；拒绝内含换行的码，避免拆成假记录。"""
    lines = []
    for record in records:
        payload = record['payload']
        if not payload:
            continue
        if '\r' in payload or '\n' in payload:
            print('Skipped payload containing a line delimiter: %r' % payload,
                  file=sys.stderr, flush=True)
            continue
        lines.append(payload.encode('utf-8') + b'\r\n')
    return b''.join(lines)


class TCPOutput:
    """长连接发当前帧；不等回复，失败丢弃，重连不补发历史码。"""
    def __init__(self, host, port):
        self.address = (host, port)
        self.sock = None
        self.retry_at = 0

    def close(self):
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def send(self, packet):
        if not packet or (self.sock is None and time.monotonic() < self.retry_at):
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
            print('TCP_UNAVAILABLE:', exc, file=sys.stderr, flush=True)
            return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--camera', required=True, help='Windows index or Linux /dev/... capture path')
    parser.add_argument('--host', required=True, help='Barcode TCP receiver IP')
    parser.add_argument('--port', required=True, type=int, help='Barcode TCP receiver port')
    parser.add_argument('--width', type=int, default=1920)
    parser.add_argument('--height', type=int, default=1080)
    parser.add_argument('--fps', type=int, default=30)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or min(args.width, args.height, args.fps) <= 0:
        parser.error('Invalid port or camera mode')
    device = int(args.camera) if args.camera.isdecimal() else args.camera
    if not isinstance(device, int) and not device.startswith('/dev/'):
        parser.error('camera must be a nonnegative index or /dev/... path')
    frames = barcode_frames(device, args.width, args.height, args.fps)
    output = TCPOutput(args.host, args.port)
    try:
        for records in frames:
            packet = encode_records(records)
            if packet:
                sys.stdout.buffer.write(packet)
                sys.stdout.buffer.flush()
                output.send(packet)
    except KeyboardInterrupt:
        return 0
    except (OSError, cv2.error) as exc:
        print('ERROR:', exc, file=sys.stderr, flush=True)
        return 1
    finally:
        frames.close()
        output.close()


if __name__ == '__main__':
    raise SystemExit(main())
