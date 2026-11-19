"""No hardware opened: real decoder, lossless synthetic video, fragmented wire, serial loopback."""
import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import scan
from scan import cv2, np, serial, zxingcpp
import wire
import struct
import openart_transport as oat


class Fragmented:
    def __init__(self, data):
        self.data = io.BytesIO(data)
    def read(self, size):
        return self.data.read(min(size, 7))


class DisplayProbe:
    """Records display publication without requiring a desktop in NUC tests."""
    def __init__(self, stop_after=None):
        self.stop = threading.Event()
        self.stop_after = stop_after
        self.rows = []
        self.state = None

    def publish(self, gray, item):
        self.rows.append((gray.copy(), item))
        if self.stop_after and len(self.rows) >= self.stop_after:
            self.stop.set()

    def finish(self, state):
        self.state = state


class Checks(unittest.TestCase):
    def setUp(self):
        self.qr = np.asarray(zxingcpp.create_barcode('11', zxingcpp.BarcodeFormat.QRCode).to_image(scale=5))
        self.bar = np.asarray(zxingcpp.create_barcode('6946522463487', zxingcpp.BarcodeFormat.Code128).to_image(scale=3))

    def canvas(self, *codes):
        result = np.full((480, 640), 255, np.uint8)
        y = 20
        for code in codes:
            if code.ndim == 3:
                code = cv2.cvtColor(code, cv2.COLOR_RGB2GRAY)
            h, w = code.shape
            result[y:y+h, 20:20+w] = code
            y += h + 20
        return result

    def test_wire_roundtrip(self):
        data = self.canvas(self.bar).tobytes()
        blob = wire.packet(data, 640, 480, 17)
        self.assertEqual(wire.receive(Fragmented(b'boot text\n'+blob)), (17,640,480,data))
        self.assertEqual(wire.receive(Fragmented(blob+blob))[0],17)

    def test_wire_rejects_corruption_and_truncation(self):
        blob = bytearray(wire.packet(bytes(4),2,2,1))
        blob[-1] ^= 1
        with self.assertRaises(ValueError): wire.receive(Fragmented(blob))
        with self.assertRaises(TimeoutError): wire.receive(Fragmented(blob[:12]), timeout=0.01)
        blob = wire.HEADER.pack(wire.MAGIC,0,640,480,0xffffffff,0)
        with self.assertRaises(ValueError): wire.receive(Fragmented(blob))

    def test_decode_multiple_and_blank(self):
        self.assertEqual(scan.decode(self.canvas()), [])
        found = {(r['type'],r['payload']) for r in scan.decode(self.canvas(self.qr,self.bar))}
        self.assertEqual(found, {('QR','11'),('CODE128','6946522463487')})

    def test_profiles_select_only_requested_code(self):
        frame = self.canvas(self.qr, self.bar)
        self.assertEqual([r['type'] for r in scan.decode(frame, 'BAR')], ['CODE128'])
        self.assertEqual([r['type'] for r in scan.decode(frame, 'QR')], ['QR'])

    def test_openart_fragmented_raw_and_jpeg(self):
        from PIL import Image
        original = self.canvas(self.bar)
        jpeg = io.BytesIO()
        Image.fromarray(original).save(jpeg, format='JPEG', quality=90)
        for size, data, transport in [(1, original.tobytes(), 'debug_raw_gray'),
                (len(jpeg.getvalue()), jpeg.getvalue(), 'debug_jpeg_preview')]:
            class Port(Fragmented):
                def write(self, packet):
                    self.commands.append(struct.unpack('<BBI', packet))
                    return len(packet)
            port = Port(struct.pack('<III',640,480,size)+data)
            port.commands = []
            camera = oat.DebugCamera(port)
            actual = np.asarray(camera.frame())
            self.assertEqual(camera.transport, transport)
            self.assertEqual(port.commands, [(0x30,0x81,12),(0x30,0x82,len(data))])
            self.assertEqual(scan.decode(actual, 'BAR')[0]['payload'], '6946522463487')
            if size == 1:
                self.assertTrue(np.array_equal(actual, original))

    def test_openart_rejects_wrong_board_before_upload(self):
        class Port(Fragmented):
            def write(self, packet):
                self.ops.append(struct.unpack('<BBI', packet)[1])
                return len(packet)
        for version, arch, expected in [(5,b'', [0x80]), (4,b'OPENMV4P STM32H743', [0x80,0x83])]:
            port=Port(struct.pack('<III',version,5,2)+arch.ljust(64,b'\0'))
            port.ops=[]
            with tempfile.TemporaryDirectory() as temp, self.assertRaises(ValueError):
                oat.DebugCamera(port).start(temp,250)
            self.assertEqual(port.ops,expected)

    def test_openart_bad_frame_and_cancel(self):
        class Port(Fragmented):
            def write(self, packet): return len(packet)
        for header in [(800,600,1),(640,480,2),(640,480,6000000)]:
            with self.assertRaises(ValueError):
                oat.DebugCamera(Port(struct.pack('<III',*header))).frame()
        with self.assertRaises(TimeoutError):
            oat.DebugCamera(Port(b''),timeout=.01).frame()
        stop=threading.Event(); stop.set()
        with self.assertRaises(InterruptedError):
            oat.DebugCamera(Port(b''),stop).frame()

    def test_openart_live_upload_receive_stop(self):
        original = self.canvas(self.bar)
        class Port:
            def __init__(self):
                self.pending = None
                self.buffer = io.BytesIO()
                self.closed = False
                self.source = b''
            def __enter__(self): return self
            def __exit__(self, *args): self.closed = True
            def read(self, size): return self.buffer.read(min(size, 997))
            def write(self, data):
                if self.pending:
                    op, size = self.pending
                    assert len(data) == size
                    if op == 0x05: self.source = data
                    else: assert data == struct.pack('<I',1)
                    self.pending = None
                else:
                    magic, op, size = struct.unpack('<BBI',data)
                    assert magic == 0x30
                    replies = {0x80: struct.pack('<III',4,5,2),
                        0x83: b'OpenMV i.MX RT10xx port'.ljust(64,b'\0'),
                        0x8e: struct.pack('<I',0), 0x87: struct.pack('<I',1),
                        0x81: struct.pack('<III',640,480,1), 0x82: original.tobytes()}
                    if op in (0x05,0x0d): self.pending = (op,size)
                    elif op == 0x06: assert size == 0
                    else:
                        assert len(replies[op]) == size
                        self.buffer = io.BytesIO(replies[op])
                return len(data)
        port=Port(); display=DisplayProbe(stop_after=1)
        with tempfile.TemporaryDirectory() as temp, patch.object(scan.serial,'Serial',return_value=port), patch.object(oat.time,'sleep'):
            args=SimpleNamespace(openart='simulated',usb=None,input=None,frames=0,every=1,
                roi=None,out=temp,code='BAR',exposure=250,setup='small lens')
            result=scan.collect(args,display)
            self.assertEqual(result['state'],'preview_stop')
            self.assertEqual(result['frames_with_decode'],1)
            self.assertTrue(port.closed)
            self.assertIn(b'PREVIEW_EXPOSURE_US = 250',port.source)
            self.assertNotIn(b'find_barcodes',port.source)
            self.assertEqual(display.rows[0][1]['capture_transport'],'debug_raw_gray')
            self.assertTrue(np.array_equal(display.rows[0][0],original))
            self.assertTrue((Path(temp)/'device.json').exists())

    def test_video_end_to_end(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root/'synthetic.avi'
            writer = cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'FFV1'),5,(640,480))
            self.assertTrue(writer.isOpened())
            for frame in [self.canvas(),self.canvas(self.qr),self.canvas(self.bar)]:
                writer.write(cv2.cvtColor(frame,cv2.COLOR_GRAY2BGR))
            writer.release()
            args = SimpleNamespace(input=str(path),usb=None,frames=3,every=1,roi=None,out=str(root/'report'))
            result = scan.run(args)
            self.assertEqual(result['frames_processed'],3)
            self.assertEqual(result['frames_with_decode'],2)
            self.assertTrue((root/'report/report.html').exists())
            rows = [json.loads(x) for x in (root/'report/frames.jsonl').read_text().splitlines()]
            self.assertEqual([r['time_ms'] for r in rows],[0,200,400])
            self.assertFalse(rows[0]['records'])

    def test_serial_result_simulation(self):
        # Demonstration JSON observation only; not the robot's mission protocol.
        packet = (json.dumps({'event':'observation','type':'QR','payload':'11'})+'\n').encode()
        with serial.serial_for_url('loop://',timeout=1) as port:
            for part in (packet[:4],packet[4:]): port.write(part)
            self.assertEqual(port.readline(),packet)

    def test_usb_live_stop_preserves_frames_and_closes_port(self):
        frames = [self.canvas(self.bar), self.canvas()]
        display = DisplayProbe(stop_after=2)

        class CameraPort:
            def __init__(self, port, baudrate, timeout, write_timeout):
                self.closed = False
                self.requests = 0
            def reset_input_buffer(self):
                pass
            def write(self, data):
                if data != b'SNAP':
                    raise ValueError('Unexpected camera request')
                self.buffer = Fragmented(wire.packet(frames[self.requests].tobytes(),640,480,self.requests))
                self.requests += 1
                return len(data)
            def read(self, size):
                return self.buffer.read(size)
            def close(self):
                self.closed = True

        port = CameraPort('unused',115200,0.2,2)
        with tempfile.TemporaryDirectory() as temp, patch.object(scan.serial, 'Serial', return_value=port):
            args = SimpleNamespace(input=None,usb='simulated',frames=0,every=1,roi=None,out=temp)
            result = scan.collect(args, display)
            self.assertEqual(result['state'],'preview_stop')
            self.assertEqual(result['frames_processed'],2)
            self.assertEqual(result['frames_with_decode'],1)
            self.assertEqual(port.requests,2)
            self.assertTrue(port.closed)
            self.assertEqual(display.rows[0][1]['records'][0]['payload'],'6946522463487')
            self.assertFalse(display.rows[1][1]['records'])
            self.assertTrue(np.array_equal(display.rows[0][0],frames[0]))
            self.assertEqual(len((Path(temp)/'frames.jsonl').read_text().splitlines()),2)

    def test_live_transport_failure_not_reported_as_no_code(self):
        display = DisplayProbe()
        with tempfile.TemporaryDirectory() as temp, patch.object(scan, 'images', side_effect=TimeoutError('image packet timeout')):
            args = SimpleNamespace(input=None,usb='simulated',frames=0,every=1,roi=None,out=temp)
            with self.assertRaises(RuntimeError):
                scan.collect(args, display)
            self.assertTrue(display.state.startswith('error: TimeoutError'))
            self.assertFalse(display.rows)
            result=json.loads((Path(temp)/'summary.json').read_text())
            self.assertEqual(result['frames_processed'],0)
            self.assertTrue(result['state'].startswith('error:'))

    def test_stop_cancels_incomplete_usb_packet(self):
        stop=threading.Event()
        class CancelStream:
            def read(self, size):
                stop.set()
                return b''
        with self.assertRaises(InterruptedError):
            wire.receive(CancelStream(),stop=stop)


if __name__ == '__main__':
    unittest.main()
