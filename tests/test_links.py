import sys
from pathlib import Path
import socket
import threading
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"host"),str(ROOT/"firmware")]
import qr_tcp
from h7_bar_usb import USBPayloadOutput

class Links(unittest.TestCase):

    def test_usb_short_writes_and_backpressure(self):

        class USB:
            data = b''
            sizes = [2, 0, None, 1, 100]

            def isconnected(self):
                return True

            def send(self, data, *, timeout=5000):
                assert timeout == 20
                n = self.sizes.pop(0) if self.sizes else 100
                if n is None:
                    return None
                n = min(n, len(data))
                self.data += bytes(data[:n])
                return n
        usb = USB()
        output = USBPayloadOutput(usb)
        output.queue([{'valid': True, 'payload': '123'}, {'valid': False, 'payload': 'bad'}, {'valid': True, 'payload': '456'}])
        for _ in range(10):
            if output.flush():
                break
        self.assertEqual(usb.data, b'123\r\n456\r\n')
        self.assertFalse(output.pending)

    def test_usb_disconnect_drops_stale_tail(self):

        class USB:
            connected = True
            data = b''

            def isconnected(self):
                return self.connected

            def send(self, data, *, timeout=5000):
                self.data += bytes(data[:1])
                return 1
        usb = USB()
        output = USBPayloadOutput(usb)
        output.queue([{'valid': True, 'payload': '123'}])
        self.assertFalse(output.flush())
        usb.connected = False
        self.assertFalse(output.flush())
        self.assertFalse(output.pending)
        usb.connected = True
        self.assertTrue(output.flush())
        output.queue([{'valid': True, 'payload': '456'}])
        while not output.flush():
            pass
        self.assertEqual(usb.data, b'1\n456\r\n')

    def test_usb_rejects_line_injection(self):
        output = USBPayloadOutput(None)
        with self.assertRaises(ValueError):
            output.queue([{'valid': True, 'payload': '123\n456'}])

    def test_tcp_continuous_without_reply(self):
        with socket.socket() as server:
            server.bind(('127.0.0.1', 0))
            server.listen()
            server.settimeout(3)
            received = []

            def read():
                with server.accept()[0] as conn:
                    conn.settimeout(3)
                    data = b''
                    while True:
                        part = conn.recv(2)
                        if not part:
                            break
                        data += part
                    received.append(data)
            worker = threading.Thread(target=read)
            worker.start()
            output = qr_tcp.TCPOutput('127.0.0.1', server.getsockname()[1])

            def rows(*values):
                return [{'type': 'QR', 'payload': v} for v in values]
            try:
                for records in [rows('13'), rows('13'), [], rows('31'), rows('99'), rows('11', '33', '11'), []]:
                    qr_tcp.send_frame(records, output)
            finally:
                output.close()
                worker.join(4)
            self.assertFalse(worker.is_alive())
            self.assertEqual(received, [b'1 3\n1 3\n3 1\n1 1\n3 3\n'])

    def test_tcp_failure_does_not_replay_old_result(self):
        from unittest.mock import Mock
        broken = Mock()
        broken.sendall.side_effect = OSError('disconnected')
        healthy = Mock()
        output = qr_tcp.TCPOutput('127.0.0.1', 9000)
        with patch.object(qr_tcp.socket, 'create_connection', side_effect=[broken, healthy]) as connect, patch.object(qr_tcp.time, 'monotonic', return_value=10):
            self.assertFalse(output.send('13'))
            self.assertFalse(output.send('11'))
            self.assertEqual(connect.call_count, 1)
        with patch.object(qr_tcp.socket, 'create_connection', return_value=healthy), patch.object(qr_tcp.time, 'monotonic', return_value=12):
            self.assertTrue(output.send('31'))
        healthy.sendall.assert_called_once_with(b'3 1\n')
        broken.close.assert_called_once()
        output.close()

    def test_main_keeps_reading_after_success_and_blank(self):
        from unittest.mock import Mock
        import numpy as np
        camera = Mock()
        frame = np.zeros((4, 4), dtype=np.uint8)
        camera.read.side_effect = [(True, frame)] * 4 + [KeyboardInterrupt()]
        camera.get.return_value = 4
        output = Mock()
        output.send.return_value = True
        records = [[{'type': 'QR', 'payload': v}] if v else [] for v in ['13', None, '31', '31']]
        with patch.object(sys, 'argv', ['qr_tcp', '--host', '127.0.0.1', '--port', '9000', '--camera', '1']), patch.object(qr_tcp.cv2, 'VideoCapture', return_value=camera), patch.object(qr_tcp, 'TCPOutput', return_value=output), patch.object(qr_tcp, 'decode', side_effect=records):
            self.assertIsNone(qr_tcp.main())
        self.assertEqual([call.args[0] for call in output.send.call_args_list], ['13', '31', '31'])
        camera.release.assert_called_once()
        output.close.assert_called_once()

    def test_invalid_qr_not_sent(self):
        with self.assertRaises(ValueError):
            qr_tcp.encode_qr('99')

if __name__ == '__main__': unittest.main()
