"""H7 UART3: P4 TX, P5 RX; payload-only UTF-8 lines, 115200 8N1.
No startup/status/error text is written to UART. USB print remains separate.
"""

class H7PayloadUART:
    def __init__(self, uart=None):
        if uart is None:
            from pyb import UART
            uart = UART(3, baudrate=115200, bits=8, parity=None, stop=1,
                        timeout=100, timeout_char=20)
        self.uart = uart

    def send(self, event):
        if event.get('event') != 'OK' or not event.get('valid'):
            raise ValueError('UART requires valid decode')
        payload = event['payload']
        if not payload or '\r' in payload or '\n' in payload:
            raise ValueError('Payload cannot contain line delimiters')
        packet = (payload + '\r\n').encode('utf-8')
        offset = 0
        while offset < len(packet):
            count = self.uart.write(packet[offset:])
            if not isinstance(count, int) or count <= 0 or count > len(packet) - offset:
                raise OSError('UART incomplete write; stop to avoid joining partial packets')
            offset += count
        return offset
