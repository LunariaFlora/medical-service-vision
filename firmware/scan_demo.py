import time
from config import (EXPOSURE_MODE, EXPOSURE_US, GAIN_DB, QR_ROI, BAR_ROI,
                    STRICT_BAR_SAMPLES, CONFIRM_FRAMES, MAX_CONFIRM_GAP_MS,
                    REARM_MS, WAIT_MS, validate_config)
from qr_scanner import scan_qr
from barcode_scanner import scan_barcodes
from result_filter import ResultFilter
from uart_output import ScanUART


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
    main()
