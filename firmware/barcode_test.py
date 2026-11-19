"""Standalone live barcode test for OpenART; run this file directly in the IDE."""
import time

EXPOSURE_US = 250
GAIN_DB = 0
SCAN_INTERVAL_MS = 300
BINARY_FALLBACK = True
BINARY_THRESHOLD = 160


def field(code, name):
    value = getattr(code, name)
    return value() if callable(value) else value


def scan(frame):
    codes = frame.find_barcodes()
    if not codes and BINARY_FALLBACK:
        # Keep the IDE preview unchanged; only the fallback copy is thresholded.
        work = frame.copy()
        work.binary([(BINARY_THRESHOLD, 255)])
        codes = work.find_barcodes()
    return codes


def main():
    import sensor
    sensor.reset()
    sensor.set_pixformat(sensor.GRAYSCALE)
    sensor.set_framesize(sensor.VGA)
    sensor.set_auto_gain(False, gain_db=GAIN_DB)
    sensor.set_auto_exposure(False, exposure_us=EXPOSURE_US)
    sensor.skip_frames(time=1500)
    print('BAR TEST v1 ready')
    last_scan = time.ticks_add(time.ticks_ms(), -SCAN_INTERVAL_MS)
    while True:
        frame = sensor.snapshot()
        now = time.ticks_ms()
        if time.ticks_diff(now, last_scan) < SCAN_INTERVAL_MS:
            time.sleep_ms(10)
            continue
        last_scan = now
        codes = scan(frame)
        for code in codes:
            print('BAR: %s' % field(code, 'payload'))
        for code in codes:
            frame.draw_rectangle(field(code, 'rect'), color=128)


if __name__ == '__main__':
    main()
