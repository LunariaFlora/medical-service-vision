UART_PORT = 12
UART_BAUDRATE = 115200


def encode_scan(event):
    if event.get('event') != 'OK' or not event.get('valid'):
        raise ValueError('Unconfirmed scan')
    kind, payload = event['kind'], event['payload']
    if kind not in ('QR', 'BAR') or not payload or any(c not in '0123456789' for c in payload):
        raise ValueError('Invalid scan packet')
    return ('%s,%s\r\n' % (kind, payload)).encode('ascii')


class ScanUART:
    def __init__(self, uart=None):
        if uart is None:
            from machine import UART
            uart = UART(UART_PORT, baudrate=UART_BAUDRATE)
        self.uart = uart

    def send(self, event):
        packet = encode_scan(event)
        offset = 0
        while offset < len(packet):
            count = self.uart.write(packet[offset:])
            if not isinstance(count, int) or count <= 0 or count > len(packet) - offset:
                raise OSError('UART write incomplete')
            offset += count
        return offset
