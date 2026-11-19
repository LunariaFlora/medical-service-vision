import io
import sys
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'firmware'))
sys.path.insert(0, str(ROOT / 'tools'))
from scan_demo import ScanDemo
from uart_output import ScanUART, encode_scan
from build_standalone import build_demo_source, build_source
from test_scanners import Code, Frame


class FakeUART:
    def __init__(self, chunk=100):
        self.data = bytearray()
        self.chunk = chunk

    def write(self, data):
        n = min(len(data), self.chunk)
        self.data.extend(data[:n])
        return n


class DemoTests(unittest.TestCase):
    def test_ide_stop_propagates_without_scan_error_or_send(self):
        for source in (build_source(), build_demo_source(), build_demo_source(True)):
            namespace = {'__name__': 'interrupt_test'}
            exec(compile(source, '<bundle>', 'exec'), namespace)
            for kind in ('QR', 'BAR'):
                frame = Frame()
                def stop(**kwargs):
                    raise Exception('IDE interrupt')
                setattr(frame, 'find_qrcodes' if kind == 'QR' else 'find_barcodes', stop)
                output = io.StringIO()
                uart = FakeUART()
                with redirect_stdout(output), self.assertRaisesRegex(Exception, 'IDE interrupt'):
                    if 'ScanDemo' in namespace:
                        namespace['ScanDemo'](0, ScanUART(uart)).process(frame, 100)
                    else:
                        namespace['decode_frame'](frame)
                self.assertEqual(output.getvalue(), '')
                self.assertEqual(uart.data, b'')
                self.assertNotIn('draw', frame.calls)

    def test_bundles_confirm_with_shadowed_dict_without_mutating_record(self):
        for source in (build_source(), build_demo_source(), build_demo_source(True)):
            namespace = {'__name__': 'shadowed_dict_test', 'dict': {}}
            exec(compile(source, '<bundle>', 'exec'), namespace)
            for kind, record in (('QR', namespace['parse_qr']('13')),
                                 ('BAR', namespace['parse_barcode']('6946522463487', True))):
                gate = namespace['ResultFilter'](kind, 0)
                self.assertEqual(gate.feed([record], 100), [])
                self.assertEqual(gate.feed([record], 200), [])
                event = gate.feed([record], 300)[0]
                self.assertEqual(event['event'], 'OK')
                self.assertEqual(event['payload'], record['payload'])
                self.assertNotIn('event', record)
                self.assertEqual(gate.feed([record], 400), [])

    def setUp(self):
        p = patch.dict(sys.modules, {'image': types.SimpleNamespace(CODE128=128)})
        p.start()
        self.addCleanup(p.stop)

    @patch('scan_demo.CONFIRM_FRAMES', 3)
    def test_confirmed_only_and_both_decoders_before_drawing(self):
        uart = FakeUART(chunk=2)
        scanner = ScanDemo(0, ScanUART(uart))
        output = io.StringIO()
        with redirect_stdout(output):
            scanner.process(Frame([Code('11')]), 100)
            scanner.process(Frame(), 200)
            scanner.process(Frame([Code('12')]), 300)
            scanner.process(Frame([Code('11'), Code('13')]), 400)
            scanner.process(Frame(), 6000)
            self.assertEqual(output.getvalue(), '')
            self.assertEqual(uart.data, b'')
            for now in (6100, 6200, 6300, 6400):
                frame = Frame([Code('13')], [Code('6946522463487')])
                scanner.process(frame, now)
                self.assertEqual(frame.calls[:2], ['QR', 'BAR'])
            self.assertEqual(frame.calls, ['QR', 'BAR', 'draw', 'draw'])
            blank = Frame()
            scanner.process(blank, 7500)
            self.assertNotIn('draw', blank.calls)
        self.assertEqual(output.getvalue(), 'QR: 13\nBAR: 6946522463487\n')
        self.assertEqual(uart.data, b'QR,13\r\nBAR,6946522463487\r\n')

    def test_new_payload_and_represented_payload(self):
        uart = FakeUART()
        scanner = ScanDemo(0, ScanUART(uart))
        with redirect_stdout(io.StringIO()):
            for now in (100, 200, 300):
                scanner.process(Frame([Code('11')]), now)
            for now in (400, 500, 600):
                scanner.process(Frame([Code('33')]), now)
            scanner.process(Frame(), 1700)
            for now in (1800, 1900, 2000):
                scanner.process(Frame([Code('33')]), now)
        self.assertEqual(uart.data, b'QR,11\r\nQR,33\r\nQR,33\r\n')

    def test_decoder_failure_is_visible_and_other_kind_still_works(self):
        output = io.StringIO()
        scanner = ScanDemo(0)
        with redirect_stdout(output):
            for now in (100, 200, 300):
                scanner.process(Frame(bar=[Code('6946522463487')], fail_qr=True), now)
        self.assertEqual(output.getvalue().count('ERROR QR:'), 1)
        self.assertIn('BAR: 6946522463487', output.getvalue())

    def test_uart_invalid_events_and_incomplete_writes(self):
        for event in ({'event': 'WAIT'}, {'event': 'OK', 'valid': False},
                      {'event': 'OK', 'valid': True, 'kind': 'QR', 'payload': '13\r\n'}):
            with self.assertRaises(ValueError):
                encode_scan(event)
        event = {'event': 'OK', 'valid': True, 'kind': 'QR', 'payload': '13'}
        for result in (None, 0, -1, 100):
            uart = types.SimpleNamespace(write=lambda data, value=result: value)
            with self.assertRaises(OSError):
                ScanUART(uart).send(event)

    def test_bundles_run_startup_and_frames_without_project_imports(self):
        class Finished(Exception):
            pass

        for enabled in (False, True):
            frames = iter([Frame([Code('31')]) for _ in range(3)])
            uart = FakeUART()
            uart_calls = []

            def make_uart(port, baudrate):
                uart_calls.append((port, baudrate))
                return uart

            def snapshot():
                try:
                    return next(frames)
                except StopIteration:
                    raise Finished()

            sensor_calls = []
            sensor = types.SimpleNamespace(GRAYSCALE=1, VGA=2, snapshot=snapshot)
            for name in ('reset', 'set_pixformat', 'set_framesize', 'set_auto_gain',
                         'set_auto_exposure', 'skip_frames'):
                setattr(sensor, name, lambda *a, _name=name, **kw: sensor_calls.append((_name, a, kw)))
            source = build_demo_source(enabled)
            for marker in ('TEST_LOG', 'DEBUG_', 'CAMERA_NOTE', 'EXPECTED', '#'):
                self.assertNotIn(marker, source)
            output = io.StringIO()
            with patch.dict(sys.modules, {'sensor': sensor,
                                          'machine': types.SimpleNamespace(UART=make_uart)}), \
                    patch('time.sleep_ms', create=True), redirect_stdout(output):
                with self.assertRaises(Finished):
                    exec(compile(source, '<demo_bundle>', 'exec'),
                         {'__name__': '__main__', 'dict': {}})
            self.assertEqual(output.getvalue(), 'QR: 31\n')
            self.assertEqual(uart_calls, [(12, 115200)] if enabled else [])
            self.assertEqual(uart.data, b'QR,31\r\n' if enabled else b'')
            self.assertIn(('set_auto_exposure', (False,), {'exposure_us': 40}), sensor_calls)
            self.assertIn(('set_framesize', (2,), {}), sensor_calls)


if __name__ == '__main__':
    unittest.main()
