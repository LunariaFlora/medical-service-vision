# GENERATED from firmware/*.py; edit the source files and rebuild.
# QR+BAR bench test; no motion. UART enabled only in h7_scan_uart.py.

# ===== firmware/config.py =====
"""当前功能验证参数。两种码每轮都检测，不设置QR/BAR二选一开关。"""

BOARD_PROFILE = 'OPENART'  # H7合并版由构建器载入config_h7.py覆盖板级参数。
AUTO_GAIN = False

EXPOSURE_MODE = 'MANUAL' # 用户看完纸质曝光组后选择手动曝光作为当前默认。
EXPOSURE_US = 40         # 2026-10-09换镜头合焦后沿用纯预览请求值，实际微秒未核实。
GAIN_DB = 0             # 与纸质曝光组一致，锁定增益以减少变量。
QR_ROI = None           # None搜索全幅；或(x, y, w, h)。
BAR_ROI = None          # 与二维码独立配置，不要求码必须居中。
BAR_BINARY_THRESHOLD = 160  # 原图未解出时，只对副本尝试旧BIN160；None关闭。
STRICT_BAR_SAMPLES = False  # 本轮测试不按6个样本拒绝Code128；原始解码始终可见。
CONFIRM_FRAMES = 1  # 两块板测试均单帧有效即输出；持续可见的相同码仍去重。
MAX_CONFIRM_GAP_MS = 2000  # 两次采样隔太久，不累计为连续确认。
REARM_MS = 1000         # 合法结果消失一秒后，可再次报告同一值。
WAIT_MS = 5000          # 每类未确认结果时只提示一次，不持续刷屏。
DEBUG_RAW = False       # True：保留的逐帧原始解码信息。
DEBUG_STATUS = False    # True：保留的每秒状态信息。
TEST_LOG = False         # 人工整批测试：打印预期集合、解码变化、带时间事件及覆盖情况。
DRAW_BOXES = True       # 必须在两种解码全部完成之后画框。


def validate_config():
    if EXPOSURE_MODE not in ('AUTO', 'MANUAL'):
        raise ValueError('EXPOSURE_MODE must be AUTO or MANUAL')
    if EXPOSURE_US <= 0 or CONFIRM_FRAMES < 1:
        raise ValueError('Exposure and confirmation count must be positive')
    if min(REARM_MS, WAIT_MS, MAX_CONFIRM_GAP_MS) <= 0:
        raise ValueError('Timing parameters must be positive')


# ===== firmware/config_h7.py =====
"""红色H7 Plus / OV5640板级覆盖；与逐飞共用识别、过滤和输出流程。
由tools/build_standalone.py插入H7单文件入口，不单独运行。
"""
BOARD_PROFILE = 'H7'
EXPOSURE_MODE = 'AUTO'
AUTO_GAIN = True

# ===== firmware/qr_scanner.py =====
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


# ===== firmware/barcode_scanner.py =====
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


# ===== firmware/result_filter.py =====
"""两类识别器各用一个独立实例，防止一类结果影响另一类。"""
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


# ===== firmware/h7_uart_output.py =====
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


# ===== firmware/main.py =====
"""功能验证入口：每一帧检查两类码，不包含比赛阶段调度或UART发送。"""
import time


def clock_ms():
    return time.ticks_ms() if hasattr(time, 'ticks_ms') else int(time.monotonic() * 1000)


def decode_frame(frame):
    # 两种解码顺序执行，均使用未画框的同一幅原图，不需要人工换模式。
    records, timings, errors = {}, {}, {}
    for kind in ('QR', 'BAR'):
        start = clock_ms()
        try:
            records[kind] = (scan_qr(frame, QR_ROI) if kind == 'QR'
                             else scan_barcodes(frame, BAR_ROI, STRICT_BAR_SAMPLES, BAR_BINARY_THRESHOLD))
        except Exception as exc:
            if 'IDE interrupt' in str(exc):
                raise
            # 一类解码器故障不阻断另一类；错误必须向测试者报告。
            records[kind] = []
            errors[kind] = str(exc)
        timings[kind] = ticks_elapsed(clock_ms(), start)
    return records, timings, errors


def draw_results(frame, records):
    # 画框放在所有解码之后。它只是显示结果，不参与本帧解码。
    if DRAW_BOXES:
        for kind in ('QR', 'BAR'):
            for record in records[kind]:
                frame.draw_rectangle(record['rect'], color=128)


def preview_status(records, filters, errors, pulse):
    lines = ['LIVE ' + '.' * (pulse % 3 + 1)]
    for kind in ('QR', 'BAR'):
        values, gate = records[kind], filters[kind]
        if kind in errors:
            state = 'ERROR'
        elif not values:
            state = 'NO CODE'
        elif len(values) > 1:
            state = 'MULTIPLE'
        elif not values[0]['valid']:
            state = 'INVALID'
        elif gate.streak >= CONFIRM_FRAMES and gate.accepted == values[0]['payload']:
            state = 'OK ' + values[0]['payload']
        else:
            state = 'CHECK %s/%s' % (gate.streak, CONFIRM_FRAMES)
        lines.append('%s: %s' % (kind, state))
    return lines


def draw_status(frame, lines):
    # Draw only after both decoders have consumed this frame.
    frame.draw_rectangle((0, 0, 640, len(lines) * 16), color=0, fill=True)
    for i, line in enumerate(lines):
        # OpenMV 4.3 consumes one three-item (x, y, text) array.
        if BOARD_PROFILE == 'H7':
            frame.draw_string((2, i * 16), line, color=255)
        else:
            frame.draw_string((2, i * 16, line), color=255)


def format_event(event):
    kind = event['kind']
    if event['event'] == 'OK':
        if kind == 'QR':
            return 'OK QR payload=%s first_bed=%s first_box=%s next_bed=%s next_box=%s' % (
                event['payload'], event['first_bed'], event['first_box'],
                event['next_bed'], event['next_box'])
        return 'OK BAR type=%s payload=%s' % (event['symbology'], event['payload'])
    if event['event'] == 'REJECT':
        return 'REJECT %s reason=%s decoded=%s' % (kind, event['reason'], event['payloads'])
    return 'WAIT %s no stable valid code' % kind


class TestReporter:
    """只增加可追溯日志，不改识别/过滤；序号不是纸张编号。"""
    def __init__(self, now):
        self.started = now
        self.seq = 0
        self.signatures = {'QR': None, 'BAR': None}
        self.last_seen = {'QR': now, 'BAR': now}
        self.seen = {'QR': [], 'BAR': []}

    def emit(self, message, now):
        self.seq += 1
        print('#%04d t_ms=%d %s' % (self.seq, ticks_elapsed(now, self.started), message))

    def expectations(self):
        print('TEST_LOG: EXPECTED=reference only; OBS=unconfirmed; OK=confirmed decode')
        print('EXPECTED QR payloads=%s' % (QR_PAYLOADS,))
        print('EXPECTED BAR CODE128 payloads=%s' % (BAR_PAYLOADS,))
        print('TEST_LOG: sequence is NOT paper ID; handwritten labels are NOT read')
        print('TEST_LOG: missing code/blur cannot be distinguished from decode output alone')

    def observe(self, kind, records, now, decode_ms):
        if records:
            # 不将框的位置变化当换码，不逐帧打印相同内容。
            signature = tuple(sorted((r['payload'], r['valid'], r.get('symbology', 'QR'), r.get('method', 'RAW'))
                                     for r in records))
            if signature != self.signatures[kind]:
                self.emit('OBS %s decoded=%s decode_ms=%s confirmed=NO' %
                          (kind, signature, decode_ms), now)
            self.signatures[kind] = signature
            self.last_seen[kind] = now
        elif self.signatures[kind] is not None and ticks_elapsed(now, self.last_seen[kind]) >= 750:
            self.emit('LOST %s no_decode_ms=%s cause=unknown' %
                      (kind, ticks_elapsed(now, self.last_seen[kind])), now)
            self.signatures[kind] = None

    def event(self, event, now):
        self.emit(format_event(event), now)
        if event['event'] != 'OK':
            return
        kind, payload = event['kind'], event['payload']
        expected = QR_PAYLOADS if kind == 'QR' else BAR_PAYLOADS
        if payload not in expected or payload in self.seen[kind]:
            return
        self.seen[kind].append(payload)
        missing_qr = [p for p in QR_PAYLOADS if p not in self.seen['QR']]
        missing_bar = [p for p in BAR_PAYLOADS if p not in self.seen['BAR']]
        self.emit('COVERAGE observed_QR=%s/4 observed_BAR=%s/6 missing_QR=%s missing_BAR=%s'
                  ' physical_match=NOT_VERIFIED' %
                  (len(self.seen['QR']), len(self.seen['BAR']), missing_qr, missing_bar), now)


def main(sender=None):
    import sensor
    try:
        import ujson as json
    except ImportError:
        import json
    validate_config()
    sensor.reset()
    sensor.set_pixformat(sensor.GRAYSCALE)
    sensor.set_framesize(sensor.VGA)
    try:
        if AUTO_GAIN:
            sensor.set_auto_gain(True)
        else:
            sensor.set_auto_gain(False, gain_db=GAIN_DB)
        if EXPOSURE_MODE == 'AUTO':
            sensor.set_auto_exposure(True)
        else:
            sensor.set_auto_exposure(False, exposure_us=EXPOSURE_US)
    except Exception as exc:
        print('CONFIG_ERROR exposure/gain: %s' % exc)
        raise
    sensor.skip_frames(time=1500)
    now = clock_ms()
    filters = {kind: ResultFilter(kind, now, CONFIRM_FRAMES, REARM_MS,
                                WAIT_MS, MAX_CONFIRM_GAP_MS) for kind in ('QR', 'BAR')}
    last_errors = {}
    last_status = now
    preview_enabled, pulse = True, 0
    reporter = TestReporter(now) if TEST_LOG else None
    print('READY board=%s continuous QR+BAR | LIVE status on preview | confirm=%s frames | UART=%s | stop in IDE' % (BOARD_PROFILE, CONFIRM_FRAMES, '3/P4/115200' if sender else 'OFF'))
    print('VGA 640x480 | exposure=%s request=%s | gain=%s | confirmed results only' %
          (EXPOSURE_MODE, EXPOSURE_US if EXPOSURE_MODE == 'MANUAL' else 'AUTO',
           'AUTO' if AUTO_GAIN else GAIN_DB))
    if reporter:
        reporter.expectations()
        for name in ('get_exposure_us', 'get_gain_db'):
            try:
                print('CAMERA %s=%s' % (name, getattr(sensor, name)()))
            except Exception:
                print('CAMERA %s=unavailable' % name)
        print('CAMERA_NOTE: exposure readback 0 on this board is not verified actual exposure')
    while True:
        frame = sensor.snapshot()
        records, timings, errors = decode_frame(frame)
        now = clock_ms()
        for kind in ('QR', 'BAR'):
            if reporter:
                reporter.observe(kind, records[kind], now, timings[kind])
            if kind in errors and last_errors.get(kind) != errors[kind]:
                print('ERROR %s decoder=%s' % (kind, errors[kind]))
            elif kind not in errors and kind in last_errors:
                print('RECOVERED %s decoder' % kind)
            # 保留逐帧详细日志；默认关闭，不删除。
            if DEBUG_RAW:
                for record in records[kind]:
                    detail = record.copy()
                    detail.update({'event': 'raw', 'decode_ms': timings[kind]})
                    print(json.dumps(detail))
            for event in filters[kind].feed(records[kind], now):
                if reporter:
                    reporter.event(event, now)
                elif event['event'] == 'OK':
                    print(format_event(event))
                if sender is not None and event['event'] == 'OK':
                    try:
                        sender.send(event)
                    except Exception as exc:
                        print('UART_ERROR %s' % exc)
                        raise
        last_errors = errors
        draw_results(frame, records)
        if preview_enabled:
            try:
                draw_status(frame, preview_status(records, filters, errors, pulse))
                pulse += 1
            except Exception as exc:
                if 'IDE interrupt' in str(exc):
                    raise
                preview_enabled = False
                print('PREVIEW_ERROR status overlay disabled; decoding continues: %s' % exc)
        # 保留每秒状态信息；需要诊断耗时和漏读时打开。
        if DEBUG_STATUS and ticks_elapsed(now, last_status) >= 1000:
            print(json.dumps({'event': 'status', 'ms': now,
                              'qr_found': len(records['QR']), 'bar_found': len(records['BAR']),
                              'qr_decode_ms': timings['QR'], 'bar_decode_ms': timings['BAR']}))
            last_status = now
        time.sleep_ms(30)


if __name__ == '__main__':
    main(sender=H7PayloadUART())

