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
