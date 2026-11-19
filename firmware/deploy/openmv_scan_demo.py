
BOARD_PROFILE = 'OPENART'
AUTO_GAIN = False

EXPOSURE_MODE = 'MANUAL'
EXPOSURE_US = 40
GAIN_DB = 0
QR_ROI = None
BAR_ROI = None
BAR_BINARY_THRESHOLD = 160
STRICT_BAR_SAMPLES = False
CONFIRM_FRAMES = 1
MAX_CONFIRM_GAP_MS = 2000
REARM_MS = 1000
WAIT_MS = 5000


def validate_config():
    if EXPOSURE_MODE not in ('AUTO', 'MANUAL'):
        raise ValueError('EXPOSURE_MODE must be AUTO or MANUAL')
    if EXPOSURE_US <= 0 or CONFIRM_FRAMES < 1:
        raise ValueError('Exposure and confirmation count must be positive')
    if min(REARM_MS, WAIT_MS, MAX_CONFIRM_GAP_MS) <= 0:
        raise ValueError('Timing parameters must be positive')


QR_PAYLOADS = ('11', '13', '31', '33')


def qr_field(code, name):
    value = getattr(code, name)
    return value() if callable(value) else value


def parse_qr(payload):
    record = {'kind': 'QR', 'payload': payload, 'valid': payload in QR_PAYLOADS}
    if record['valid']:
        bed, box = int(payload[0]), int(payload[1])
        record.update({'first_bed': bed, 'first_box': box,
                       'next_bed': 4 - bed, 'next_box': 4 - box})
    else:
        record['reason'] = 'unexpected_qr_payload'
    return record


def scan_qr(frame, roi=None):

    codes = frame.find_qrcodes() if roi is None else frame.find_qrcodes(roi=roi)
    records = []
    for code in codes:
        record = parse_qr(qr_field(code, 'payload'))
        record['rect'] = qr_field(code, 'rect')
        records.append(record)

    return records


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

import time


def ticks_elapsed(now, then):
    return time.ticks_diff(now, then) if hasattr(time, 'ticks_diff') else now - then


class ResultFilter:
    def __init__(self, kind, now, confirm_frames=3, rearm_ms=1000,
                 wait_ms=5000, max_gap_ms=2000):
        self.kind = kind
        self.confirm_frames = confirm_frames
        self.rearm_ms, self.wait_ms, self.max_gap_ms = rearm_ms, wait_ms, max_gap_ms
        self.candidate, self.streak, self.accepted = None, 0, None
        self.last_valid = self.last_frame = self.wait_from = now
        self.wait_printed = False
        self.last_warning = None
        self.warning_at = None

    def feed(self, records, now):
        events = []
        if ticks_elapsed(now, self.last_valid) >= self.rearm_ms:
            self.accepted = None
        if ticks_elapsed(now, self.last_frame) > self.max_gap_ms:
            self.candidate, self.streak = None, 0
        self.last_frame = now
        valid = (len(records) == 1 and records[0]['kind'] == self.kind
                 and records[0]['valid'])
        if valid:
            record = records[0]
            payload = record['payload']
            self.last_valid, self.last_warning = now, None
            if payload == self.candidate:
                self.streak += 1
            else:
                self.candidate, self.streak = payload, 1
            if self.streak >= self.confirm_frames:
                self.wait_from, self.wait_printed = now, False
                if payload != self.accepted:
                    event = record.copy()
                    event['event'] = 'OK'
                    events.append(event)
                    self.accepted = payload
        else:
            self.candidate, self.streak = None, 0
            if records:
                reason = 'multiple_codes_of_same_kind' if len(records) > 1 else records[0].get('reason', 'unexpected_kind')
                values = tuple(r['payload'] for r in records)
                warning = (reason, values)
                if warning != self.last_warning and (self.warning_at is None or ticks_elapsed(now, self.warning_at) >= 1000):
                    events.append({'event': 'REJECT', 'kind': self.kind,
                                   'reason': reason, 'payloads': values})
                    self.last_warning, self.warning_at = warning, now
        if not self.wait_printed and ticks_elapsed(now, self.wait_from) >= self.wait_ms:
            events.append({'event': 'WAIT', 'kind': self.kind,
                           'reason': 'no_stable_valid_code'})
            self.wait_printed = True
        return events

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

import time


def demo_clock_ms():
    return time.ticks_ms() if hasattr(time, 'ticks_ms') else int(time.monotonic() * 1000)


class ScanDemo:
    def __init__(self, now, sender=None):
        self.sender = sender
        self.errors = {}
        self.filters = {kind: ResultFilter(kind, now, CONFIRM_FRAMES, REARM_MS,
                                          WAIT_MS, MAX_CONFIRM_GAP_MS)
                        for kind in ('QR', 'BAR')}

    def process(self, frame, now=None):
        records, errors = {}, {}
        for kind in ('QR', 'BAR'):
            try:
                records[kind] = (scan_qr(frame, QR_ROI) if kind == 'QR' else
                                 scan_barcodes(frame, BAR_ROI, STRICT_BAR_SAMPLES))
            except Exception as exc:
                if 'IDE interrupt' in str(exc):
                    raise
                records[kind] = []
                errors[kind] = str(exc)
                if self.errors.get(kind) != errors[kind]:
                    print('ERROR %s: %s' % (kind, exc))
        self.errors = errors
        if now is None:
            now = demo_clock_ms()
        for kind in ('QR', 'BAR'):
            gate = self.filters[kind]
            for event in gate.feed(records[kind], now):
                if event['event'] == 'OK':
                    print('%s: %s' % (kind, event['payload']))
                    if self.sender is not None:
                        self.sender.send(event)
        for kind in ('QR', 'BAR'):
            gate = self.filters[kind]
            if (len(records[kind]) == 1 and records[kind][0]['valid'] and
                    gate.streak >= CONFIRM_FRAMES and
                    gate.accepted == records[kind][0]['payload']):
                frame.draw_rectangle(records[kind][0]['rect'], color=128)


def main(enable_uart=False):
    import sensor
    validate_config()
    sender = ScanUART() if enable_uart else None
    sensor.reset()
    sensor.set_pixformat(sensor.GRAYSCALE)
    sensor.set_framesize(sensor.VGA)
    sensor.set_auto_gain(False, gain_db=GAIN_DB)
    if EXPOSURE_MODE == 'AUTO':
        sensor.set_auto_exposure(True)
    else:
        sensor.set_auto_exposure(False, exposure_us=EXPOSURE_US)
    sensor.skip_frames(time=1500)
    scanner = ScanDemo(demo_clock_ms(), sender)
    while True:
        scanner.process(sensor.snapshot())
        time.sleep_ms(30)



if __name__ == '__main__':
    main(enable_uart=False)
