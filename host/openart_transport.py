"""Legacy OpenART USB debug adapter, extracted from 历史实验/host_runner.py.
Temporary RAM preview only; no flash/SD writes. Not H7 RCF1 or USB-UART.
"""
import io
import json
from pathlib import Path
import struct
import time


class DebugCamera:
    def __init__(self, port, stop=None, timeout=5):
        self.port, self.stop, self.timeout = port, stop, timeout
        self.transport = None

    def cancelled(self):
        if self.stop is not None and self.stop.is_set():
            raise InterruptedError('Preview stopped')

    def write(self, data):
        self.cancelled()
        if self.port.write(data) != len(data):
            raise OSError('OpenART USB short write')

    def command(self, op, size=0):
        self.write(struct.pack('<BBI', 0x30, op, size))

    def exact(self, size):
        result = bytearray()
        end = time.monotonic() + self.timeout
        while len(result) < size:
            self.cancelled()
            if time.monotonic() >= end:
                raise TimeoutError('OpenART USB short read: %d/%d; reconnect board' % (len(result), size))
            result.extend(self.port.read(size-len(result)))
        return bytes(result)

    def u32(self, op):
        self.command(op, 4)
        return struct.unpack('<I', self.exact(4))[0]

    def output(self):
        count = self.u32(0x8e)
        if count > 1000000:
            raise ValueError('Invalid OpenART text length')
        if count:
            self.command(0x8f, count)
            return self.exact(count)
        return b''

    def start(self, out, exposure):
        self.command(0x80, 12)
        version = struct.unpack('<III', self.exact(12))
        # Only the previously observed legacy family. Do not send H7 stop/upload commands.
        if version[0] != 4:
            raise ValueError('Not the supported OpenART legacy protocol: %s' % (version,))
        self.command(0x83, 64)
        arch = self.exact(64).split(b'\0')[0].decode(errors='replace')
        if 'i.MX RT' not in arch:
            raise ValueError('Wrong board for OpenART profile: ' + arch)
        Path(out, 'device.json').write_text(json.dumps({'usb_debug_version': version,
            'arch': arch, 'exposure_request': exposure, 'physical_sensor': 'not read back',
            'lens': 'user-installed H7 small lens; sensor unchanged'}, indent=2), encoding='utf8')
        print('OPENART_DEVICE', version, arch, flush=True)
        self.command(0x06)
        time.sleep(0.2)
        self.output()  # drain output from the stopped RAM script
        source = (Path(__file__).resolve().parents[1]/'firmware/camera_preview.py').read_text(encoding='utf8')
        source = 'PREVIEW_EXPOSURE_US = %r\n' % exposure + source
        Path(out, 'executed_preview.py').write_text(source, encoding='utf8')
        self.command(0x0d, 4)
        self.write(struct.pack('<I', 1))
        data = source.encode('utf8')
        self.command(0x05, len(data))
        self.write(data)
        # Allow sensor startup, while preserving responsive cancellation.
        for _ in range(30):
            self.cancelled()
            time.sleep(0.1)

    def frame(self):
        from PIL import Image
        self.command(0x81, 12)
        width, height, size = struct.unpack('<III', self.exact(12))
        if width == 0 or height == 0:
            return None
        if not (0 < width <= 640 and 0 < height <= 480):
            raise ValueError('Unexpected OpenART frame dimensions')
        if size == 2:
            raise ValueError('RGB565 returned; expected grayscale preview')
        count = width * height if size == 1 else size
        if not 0 < count <= 5000000:
            raise ValueError('Invalid OpenART image length')
        self.command(0x82, count)
        data = self.exact(count)
        if size == 1:
            self.transport = 'debug_raw_gray'
            return Image.frombytes('L', (width, height), data)
        self.transport = 'debug_jpeg_preview'
        with Image.open(io.BytesIO(data)) as image:
            if image.format != 'JPEG' or image.size != (width, height):
                raise ValueError('Unexpected USB preview encoding/dimensions')
            return image.convert('L')


def frames(args, stop=None):
    import serial
    import numpy as np
    with serial.Serial(args.openart, 921600, timeout=0.2, write_timeout=2) as port:
        camera = DebugCamera(port, stop)
        camera.start(args.out, getattr(args, 'exposure', 250))
        started = last_frame = time.monotonic()
        index = 0
        with Path(args.out, 'board_output.txt').open('wb') as log:
            while args.frames == 0 or index < args.frames:
                camera.cancelled()
                output = camera.output()
                if output:
                    log.write(output); log.flush()
                image = camera.frame()
                if image is not None:
                    args.capture_transport = camera.transport
                    yield index, (time.monotonic()-started)*1000, np.asarray(image).copy()
                    last_frame = time.monotonic()
                    index += 1
                elif time.monotonic()-last_frame > 10:
                    raise TimeoutError('OpenART produced no frame; see board_output.txt')
                if not camera.u32(0x87):
                    log.write(camera.output()); log.flush()
                    raise RuntimeError('OpenART preview script stopped; see board_output.txt')
                time.sleep(0.03)
    # Closing the port leaves only the capture-only RAM script running.
    # No asynchronous stop sent into a possibly incomplete transfer. IDE can stop it.
