"""分辨率对照：IDE直接运行。默认仅预览，摆位确认后手动开启TEST。
不写文件、不用UART；正式双类入口和此前诊断文件保持原样。
"""
import gc
import time

RUN_TEST = True  # 先预览；纸和镜头固定好后停止，将这里改为True，再运行。
PROFILE_TO_TEST = 'SVGA'  # 当前一次仅测一档；可选PROFILES内的label。
TRACE_STEPS = True  # BEGIN后没有DONE/FAIL表示该调用尚未返回，不是解码空结果。
EXPOSURE_US = 250
GAIN_DB = 0
BIN_THRESHOLD = 160
FRAMES_PER_PROFILE = 3
# label, sensor常量名, 请求的window, 预期输出尺寸。
PROFILES = (
    ('VGA_START', 'VGA', None, (640, 480)),
    ('SVGA', 'SVGA', None, (800, 600)),
    ('XGA', 'XGA', None, (1024, 768)),
    ('SXGA', 'SXGA', None, (1280, 1024)),
    ('SXGA_CROP', 'SXGA', (1280, 960), (1280, 960)),
    ('VGA_END', 'VGA', None, (640, 480)),
)


def check_stop(exc):
    if 'IDE interrupt' in str(exc):
        raise exc


def field(obj, name):
    value = getattr(obj, name)
    return value() if callable(value) else value


def optional_call(obj, name):
    try:
        return getattr(obj, name)()
    except Exception as exc:
        check_stop(exc)
        return 'unavailable'


def step(label, name, action):
    if TRACE_STEPS:
        print('STEP %s %s BEGIN' % (label, name))
        time.sleep_ms(60)  # 给IDE机会收到定位行；这不是阻塞调用的超时器。
    try:
        result = action()
    except Exception as exc:
        print('STEP %s %s FAIL %s: %s' % (label, name, type(exc).__name__, exc))
        raise
    if TRACE_STEPS:
        print('STEP %s %s DONE' % (label, name))
    return result


def configure(sensor, profile):
    label, constant, window, _ = profile
    step(label, 'reset', lambda: sensor.reset())
    step(label, 'pixformat', lambda: sensor.set_pixformat(sensor.GRAYSCALE))
    step(label, 'framesize', lambda: sensor.set_framesize(getattr(sensor, constant)))
    if window is not None:
        step(label, 'windowing', lambda: sensor.set_windowing(window))
    step(label, 'gain', lambda: sensor.set_auto_gain(False, gain_db=GAIN_DB))
    step(label, 'exposure', lambda: sensor.set_auto_exposure(False, exposure_us=EXPOSURE_US))
    step(label, 'skip_frames', lambda: sensor.skip_frames(time=1200))


def decode_variant(frame, threshold=None):
    work = None
    started = time.ticks_ms()
    try:
        work = frame if threshold is None else frame.copy()
        if threshold is not None:
            work.binary([(threshold, 255)])
        records = tuple((field(code, 'payload'), field(code, 'type'),
                         field(code, 'rect')) for code in work.find_barcodes())
        return records, time.ticks_diff(time.ticks_ms(), started), None
    except Exception as exc:
        check_stop(exc)
        return (), time.ticks_diff(time.ticks_ms(), started), '%s: %s' % (
            type(exc).__name__, exc)
    finally:
        work = None


def run_profile(sensor, profile):
    label, _, window, expected = profile
    summary = {'profile': label, 'state': 'COMPLETE', 'frames': 0,
               'raw_hit_frames': 0, 'bin_hit_frames': 0,
               'raw_errors': 0, 'bin_errors': 0}
    frame = None
    print('PROFILE %s expected=%s window=%s' % (label, expected, window))
    gc.collect()
    try:
        configure(sensor, profile)
    except Exception as exc:
        check_stop(exc)
        summary['state'] = 'CONFIG_ERROR'
        print('CONFIG_ERROR %s %s: %s' % (label, type(exc).__name__, exc))
        return summary
    print('META %s sensor_id=%s exposure_readback=%s gain_readback=%s' %
          (label, step(label, 'get_id', lambda: optional_call(sensor, 'get_id')),
           step(label, 'get_exposure_us', lambda: optional_call(sensor, 'get_exposure_us')),
           step(label, 'get_gain_db', lambda: optional_call(sensor, 'get_gain_db'))))
    try:
        for index in range(FRAMES_PER_PROFILE):
            frame = None
            gc.collect()
            try:
                frame = step(label, 'snapshot_%d' % (index + 1), lambda: sensor.snapshot())
            except Exception as exc:
                check_stop(exc)
                summary['state'] = 'CAPTURE_ERROR'
                print('CAPTURE_ERROR %s %s: %s' % (label, type(exc).__name__, exc))
                break
            actual = (frame.width(), frame.height())
            if actual != expected:
                summary['state'] = 'SIZE_MISMATCH'
                print('SIZE_MISMATCH %s expected=%s actual=%s; skip this profile' %
                      (label, expected, actual))
                break  # 不把驱动回退/内存裁剪的低分辨率冒充高分辨率。
            try:
                mean = step(label, 'statistics', lambda: frame.get_statistics().mean())
            except Exception as exc:
                check_stop(exc)
                mean = 'unavailable'
            raw, raw_ms, raw_error = step(label, 'RAW_decode', lambda: decode_variant(frame))
            binary, bin_ms, bin_error = step(
                label, 'BIN160_decode', lambda: decode_variant(frame, BIN_THRESHOLD))
            summary['frames'] += 1
            summary['raw_hit_frames'] += bool(raw)
            summary['bin_hit_frames'] += bool(binary)
            summary['raw_errors'] += raw_error is not None
            summary['bin_errors'] += bin_error is not None
            print('FRAME %s %d/%d actual=%s mean=%s RAW=%s raw_ms=%d '
                  'BIN160=%s bin_ms=%d ERR=%s' %
                  (label, index + 1, FRAMES_PER_PROFILE, actual, mean, raw, raw_ms,
                   binary, bin_ms, (raw_error, bin_error)))
            time.sleep_ms(100)
    finally:
        frame = None
        gc.collect()
    if summary['state'] == 'COMPLETE' and (summary['raw_errors'] or summary['bin_errors']):
        summary['state'] = 'COMPLETE_WITH_ERRORS'
    return summary


def run_test(sensor, profiles=None):
    if profiles is None:
        profiles = tuple(p for p in PROFILES if p[0] == PROFILE_TO_TEST)
        if not profiles:
            raise ValueError('Unknown PROFILE_TO_TEST: %s' % PROFILE_TO_TEST)
    print('TEST_START | single-profile default; keep paper/camera/light fixed')
    print('If a STEP has BEGIN but no DONE/FAIL for 15 seconds, stop and report last STEP')
    print('Native driver calls may block; Python exceptions cannot guarantee a timeout')
    print('Records=(payload,type,rect); frame counts are NOT independent success rates')
    results = []
    for profile in profiles:
        result = run_profile(sensor, profile)
        results.append(result)
        print('SUMMARY %s' % result)
    print('TEST_DONE | send complete log; no automatic winner or settings change')
    return results


def main():
    import sensor

    print('BAR_RESOLUTION v2 mode=%s profile=%s' %
          ('TEST' if RUN_TEST else 'PREVIEW', PROFILE_TO_TEST))
    print('Requested exposure=%dus gain=%s gray; readback 0 is not verified zero exposure' %
          (EXPOSURE_US, GAIN_DB))
    if RUN_TEST:
        run_test(sensor)
        return
    configure(sensor, PROFILES[0])
    print('PREVIEW_ONLY | fix paper and camera; no test samples or files are being saved')
    print('When ready: stop, change RUN_TEST=False to True, run again; no need to move paper')
    while True:
        sensor.snapshot()
        time.sleep_ms(50)


if __name__ == '__main__':
    main()
