import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'firmware'))
from h7_uart_output import H7PayloadUART

class PayloadUARTTests(unittest.TestCase):
    def test_constructor_and_payload_only(self):
        calls, output = [], bytearray()
        class UART:
            def __init__(self, port, *, baudrate, bits, parity, stop, timeout, timeout_char):
                calls.append((port, baudrate, bits, parity, stop, timeout, timeout_char))
            def write(self, data):
                output.extend(data)
                return len(data)
        with patch.dict(sys.modules, {'pyb': types.SimpleNamespace(UART=UART)}):
            sender = H7PayloadUART()
        self.assertEqual(output, b'')
        self.assertEqual(calls, [(3, 115200, 8, None, 1, 100, 20)])
        sender.send({'event': 'OK', 'valid': True, 'payload': '6944060407291'})
        self.assertEqual(output, b'6944060407291\r\n')

    def test_invalid_events_and_delimiters_never_reach_wire(self):
        class UART:
            def write(self, data):
                raise AssertionError('must not transmit')
        sender = H7PayloadUART(UART())
        for event in ({'event': 'WAIT'}, {'event': 'OK', 'valid': False},
                      {'event': 'OK', 'valid': True, 'payload': '11\nERROR'}):
            with self.assertRaises(ValueError):
                sender.send(event)

    def test_incomplete_write_stops_without_error_text_or_infinite_retry(self):
        for result in (None, 0, -1, 999):
            class UART:
                def write(self, data):
                    return result
            with self.assertRaises(OSError):
                H7PayloadUART(UART()).send({'event': 'OK', 'valid': True, 'payload': '11'})
