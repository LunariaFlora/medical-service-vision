"""功能验证入口：每一帧检查两类码，不包含比赛阶段调度或UART发送。"""
import time
from config import (BOARD_PROFILE, AUTO_GAIN, EXPOSURE_MODE, EXPOSURE_US, GAIN_DB, QR_ROI, BAR_ROI,
                    BAR_BINARY_THRESHOLD,
                    STRICT_BAR_SAMPLES, CONFIRM_FRAMES, MAX_CONFIRM_GAP_MS,
                    REARM_MS, WAIT_MS, DEBUG_RAW, DEBUG_STATUS, TEST_LOG, DRAW_BOXES,
                    validate_config)
from qr_scanner import scan_qr, QR_PAYLOADS
from barcode_scanner import scan_barcodes, BAR_PAYLOADS
from result_filter import ResultFilter, ticks_elapsed


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
    main()
