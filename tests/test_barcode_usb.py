"""独立USB条码程序：真实ZXing解码、TCP无回复持续发送、故障不重放。"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _folder in ('firmware', 'host'):
    if str(_ROOT / _folder) not in sys.path:
        sys.path.insert(0, str(_ROOT / _folder))
import io
import socket
import threading
import unittest
from unittest.mock import Mock, patch
import numpy as np
import barcode_usb as app


class BarcodeUSBTests(unittest.TestCase):
    def test_real_decoder_and_blank(self):
        for payload in ('6944060407291', '12345678'):
            image = np.asarray(app.zxingcpp.create_barcode(
                payload, app.zxingcpp.BarcodeFormat.Code128).to_image(scale=3))
            records = app.decode_barcodes(image)
            self.assertEqual([r['payload'] for r in records], [payload])
        self.assertEqual(app.decode_barcodes(np.full((200, 600), 255, np.uint8)), [])

    def test_tcp_continuous_no_ack(self):
        received = []
        with socket.socket() as server:
            server.bind(('127.0.0.1', 0)); server.listen(); server.settimeout(3)
            def receive():
                with server.accept()[0] as conn:
                    conn.settimeout(3)
                    data = b''
                    while True:
                        chunk = conn.recv(2)
                        if not chunk: break
                        data += chunk
                    received.append(data)
            worker = threading.Thread(target=receive); worker.start()
            output = app.TCPOutput('127.0.0.1', server.getsockname()[1])
            try:
                for values in (['6944060407291'], [], ['6944060407291'],
                               ['6906841121017', '6938237700261']):
                    packet = app.encode_records([{'payload':v} for v in values])
                    output.send(packet)
            finally:
                output.close(); worker.join(4)
            self.assertFalse(worker.is_alive())
            self.assertEqual(received, [b'6944060407291\r\n6944060407291\r\n6906841121017\r\n6938237700261\r\n'])

    def test_line_boundaries(self):
        self.assertEqual(app.encode_records([{'payload':''}, {'payload':'12\n34'},
                                            {'payload':'12\r34'}, {'payload':'567'}]), b'567\r\n')

    def test_reconnect_only_new_frame(self):
        broken = Mock(); broken.sendall.side_effect = OSError('lost')
        fresh = Mock(); out = app.TCPOutput('localhost', 9001)
        with patch.object(app.socket, 'create_connection', return_value=broken), \
             patch.object(app.time, 'monotonic', return_value=10):
            self.assertFalse(out.send(b'old\r\n'))
            self.assertFalse(out.send(b'dropped\r\n'))
        with patch.object(app.socket, 'create_connection', return_value=fresh), \
             patch.object(app.time, 'monotonic', return_value=12):
            self.assertFalse(out.send(b''))
            self.assertTrue(out.send(b'new\r\n'))
        fresh.sendall.assert_called_once_with(b'new\r\n')
        out.close()

    def test_capture_released_on_close(self):
        camera = Mock(); camera.get.return_value = 100
        camera.read.return_value = (True, np.full((100, 100), 255, np.uint8))
        with patch.object(app.cv2, 'VideoCapture', return_value=camera):
            frames = app.barcode_frames(1)
            self.assertEqual(next(frames), [])
            frames.close()
        camera.release.assert_called_once()


if __name__ == '__main__': unittest.main()
