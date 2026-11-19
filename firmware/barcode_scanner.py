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
