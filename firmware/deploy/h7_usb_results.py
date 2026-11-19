# GENERATED: H7 native decode -> USB results; no image streaming.

"""护士台二维码的图像解码与含义解析；不控制车辆、不发送串口。"""

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
    # 解码器会查找二维码，而非OCR读取码下方的数字。
    codes = frame.find_qrcodes() if roi is None else frame.find_qrcodes(roi=roi)
    records = []
    for code in codes:
        record = parse_qr(qr_field(code, 'payload'))
        record['rect'] = qr_field(code, 'rect')
        records.append(record)
    # 此处不画框，避免改变后续条形码解码所使用的原图。
    return records

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

"""H7: reuse native decoders; send every sampled frame result over USB, no UART."""


def stripe_hint(frame):
    # Coarse appearance hint, not a barcode detector. No image modification.
    pixels = frame.bytearray()
    w, h = frame.width(), frame.height()
    for vertical in (False, True):
        extent, cross = (h,w) if vertical else (w,h)
        for line in range(20, cross, 60):
            previous, sign, turns, low, high = None, 0, 0, 255, 0
            for position in range(0, extent, 4):
                v = pixels[position*w+line] if vertical else pixels[line*w+position]
                low, high = min(low,v), max(high,v)
                if previous is not None and abs(v-previous) >= 16:
                    direction = 1 if v > previous else -1
                    if sign and direction != sign:
                        turns += 1
                    sign = direction
                previous = v
            if turns >= 10 and high-low >= 48:
                return True
    return False


def sample(frame, include_qr=True, timings=None):
    import time
    clock = time.ticks_ms if hasattr(time, 'ticks_ms') else lambda: int(time.perf_counter()*1000)
    elapsed = time.ticks_diff if hasattr(time, 'ticks_diff') else lambda a,b: a-b
    records, errors = [], {}
    decoders = [('BAR', lambda: scan_barcodes(frame, strict_samples=False, binary_threshold=160, timings=timings))]
    if include_qr: decoders.insert(0, ('QR', lambda: scan_qr(frame)))
    if timings is not None: timings['qr_ms'] = 0
    for kind, decoder in decoders:
        start = clock()
        try:
            for row in decoder():
                row['type'] = 'QR' if kind == 'QR' else row['symbology']
                records.append(row)
        except Exception as exc:
            errors[kind] = str(exc)
        if timings is not None: timings[kind.lower()+'_ms'] = elapsed(clock(), start)
    start = clock()
    hint = None
    if not errors:
        try:
            hint = any(r['type']=='CODE128' for r in records) or stripe_hint(frame)
        except Exception as exc:
            errors['appearance'] = str(exc)
    if timings is not None: timings['appearance_ms'] = elapsed(clock(), start)
    return records, errors, hint


def main():
    import sensor, time, struct, ubinascii, os
    import json
    from pyb import USB_VCP
    sensor.reset()
    sensor.set_pixformat(sensor.GRAYSCALE)
    sensor.set_framesize(sensor.VGA)
    sensor.set_auto_gain(True)
    sensor.set_auto_exposure(True)
    sensor.skip_frames(time=2000)
    usb = USB_VCP()
    usb.setinterrupt(-1)
    boot = ubinascii.hexlify(os.urandom(4)).decode()
    seq, command = 0, b''
    def send_all(data):
        view, offset = memoryview(data), 0
        deadline = time.ticks_add(time.ticks_ms(), 3000)
        while offset < len(view):
            if time.ticks_diff(deadline,time.ticks_ms()) <= 0:
                return False
            n = usb.send(view[offset:offset+1024],timeout=100)
            if n: offset += n
        return True
    while True:
        command += usb.recv(4-len(command),timeout=100) or b''
        if len(command) < 4: continue
        if command not in (b'SCAN', b'BARS', b'PICB', b'PICQ'):
            command = command[-3:]
            continue
        include_qr = command in (b'SCAN', b'PICQ')
        capture_image = command in (b'PICB', b'PICQ')
        command = b''
        started = time.ticks_ms()
        frame = sensor.snapshot()
        stages = {'snapshot_ms':time.ticks_diff(time.ticks_ms(),started)}
        records, errors, hint = sample(frame, include_qr=include_qr, timings=stages)
        body = json.dumps({'protocol':1,'boot':boot,'seq':seq,
                           'records':records,'errors':errors,'barcode_like':hint,
                           'scan_mode':'BOTH' if include_qr else 'BAR', 'stages_ms':stages,
                           'has_image':capture_image,
                           'board_ms':time.ticks_diff(time.ticks_ms(),started)}).encode()
        header = struct.pack('<4sII',b'RCD1',len(body),ubinascii.crc32(body)&0xffffffff)
        sent = send_all(header) and send_all(body)
        if sent and capture_image:
            pixels = frame.bytearray()
            image_header = struct.pack('<4sIHHII', b'RCF1', seq, frame.width(), frame.height(), len(pixels), ubinascii.crc32(pixels)&0xffffffff)
            if send_all(image_header): send_all(pixels)
        seq += 1


if __name__ == '__main__':
    main()
