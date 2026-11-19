"""H7 raw Code128 -> USB VCP payload lines. No UART or diagnostic prints."""
from barcode_scanner import scan_barcodes


class USBPayloadOutput:
    def __init__(self, usb):
        self.usb = usb
        self.pending = b''
        self.offset = 0
        self.resync = False

    def queue(self, records):
        if self.pending:
            raise RuntimeError('Previous frame still pending')
        lines = []
        for row in records:
            if row['valid']:
                payload = row['payload']
                if not payload or '\r' in payload or '\n' in payload:
                    raise ValueError('Payload cannot contain line delimiters')
                lines.append(payload.encode('utf-8') + b'\r\n')
        self.pending = b''.join(lines)
        self.offset = 0

    def flush(self):
        if not self.usb.isconnected():
            # Do not replay old observations after disconnect. Terminate any
            # partial line on reconnection; host must clear its buffer on reopen.
            self.resync = self.resync or self.offset > 0
            self.pending = b''
            self.offset = 0
            return False
        if self.resync:
            n = self.usb.send(b'\n', timeout=20)
            if n != 1:
                return False
            self.resync = False
        if not self.pending:
            return True
        n = self.usb.send(memoryview(self.pending)[self.offset:], timeout=20)
        if n is None or n == 0:
            return False
        if not isinstance(n, int) or n < 0 or n > len(self.pending)-self.offset:
            raise OSError('Invalid USB send length')
        self.offset += n
        if self.offset == len(self.pending):
            self.pending = b''
            self.offset = 0
            return True
        return False


def run():
    import sensor
    import time
    from pyb import USB_VCP
    usb = USB_VCP()
    usb.setinterrupt(-1)
    output = USBPayloadOutput(usb)
    sensor.reset()
    sensor.set_pixformat(sensor.GRAYSCALE)
    sensor.set_framesize(sensor.VGA)
    sensor.set_auto_exposure(True)
    sensor.set_auto_gain(True)
    sensor.skip_frames(time=2000)
    while True:
        if not output.flush():
            time.sleep_ms(10)
            continue
        # RAW_ONLY: one decoder call per frame, no binary fallback.
        records = scan_barcodes(sensor.snapshot(), strict_samples=False)
        output.queue(records)


def main():
    try:
        run()
    except Exception:
        # Keep traceback/REPL text out of the payload channel. Red LED means
        # restart and diagnose in IDE; never emit an error as a barcode.
        from pyb import LED
        import time
        LED(1).on()
        while True:
            time.sleep_ms(1000)


if __name__ == '__main__':
    main()
