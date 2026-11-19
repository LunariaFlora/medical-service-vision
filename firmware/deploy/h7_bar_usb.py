# GENERATED: H7 RAW_ONLY -> USB VCP payload CRLF; no UART.

"""床头柜条形码解码；payload不是床号，暂不做床位绑定。"""

BAR_PAYLOADS = ('6946522463487', '6921361255288', '6911345321863',
                '6944060407291', '6906841121017', '6938237700261')


def bar_field(code, name):
    value = getattr(code, name)
    return value() if callable(value) else value


def parse_barcode(payload, is_code128, strict_samples=True):
    known = payload in BAR_PAYLOADS
    valid = bool(payload) and is_code128 and (known or not strict_samples)
    record = {'kind': 'BAR', 'payload': payload, 'valid': bool(valid),
              'symbology': 'CODE128' if is_code128 else 'OTHER',
              'known_sample': known}
    if not valid:
        record['reason'] = ('unexpected_barcode_type' if not is_code128
                            else 'unexpected_barcode_payload')
    return record


def scan_barcodes(frame, roi=None, strict_samples=True, binary_threshold=None, timings=None):
    import image
    import time
    clock = time.ticks_ms if hasattr(time, 'ticks_ms') else lambda: int(time.perf_counter()*1000)
    elapsed = time.ticks_diff if hasattr(time, 'ticks_diff') else lambda a,b: a-b
    start = clock()
    codes = frame.find_barcodes() if roi is None else frame.find_barcodes(roi=roi)
    if timings is not None:
        timings['bar_raw_ms'] = elapsed(clock(), start)
        timings['binary_ms'] = 0
        timings['bar_binary_ms'] = 0
    method = 'RAW'
    if not codes and binary_threshold is not None:
        start = clock()
        work = frame.copy()
        work.binary([(binary_threshold, 255)])
        if timings is not None: timings['binary_ms'] = elapsed(clock(),start)
        start = clock()
        codes = work.find_barcodes() if roi is None else work.find_barcodes(roi=roi)
        if timings is not None: timings['bar_binary_ms'] = elapsed(clock(),start)
        method = 'BIN%d' % binary_threshold
    records = []
    for code in codes:
        type_id = bar_field(code, 'type')
        record = parse_barcode(bar_field(code, 'payload'),
                               type_id == image.CODE128, strict_samples)
        record['type_id'] = type_id
        record['method'] = method
        record['rect'] = bar_field(code, 'rect')
        records.append(record)
    return records

"""H7 raw Code128 -> USB VCP payload lines. No UART or diagnostic prints."""


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
